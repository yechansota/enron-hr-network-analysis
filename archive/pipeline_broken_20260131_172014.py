import csv
import re
import sys
import time
from collections import deque, defaultdict

import numpy as np
import pandas as pd
import networkx as nx
from networkx.algorithms import community
import matplotlib.pyplot as plt
from dateutil import parser

csv.field_size_limit(sys.maxsize)

SKIP_SUBJECTS = {
    "accepted:", "declined:", "tentative:", "automatic reply:", "out of office",
    "demand for payment", "newsletter", "daily report", "status change"
}
MIN_BODY_LENGTH = 30

RT_MIN_HOURS = 0.1
RT_MAX_HOURS = 168
SLOW_THRESHOLD_H = 24

EI_OPEN_THRESHOLD = -0.2
VALID_DOMAINS = {"enron.com"}
BETWEENNESS_SAMPLE_K = 200

EI_MODE = "both"
EI_FOR_TYPOLOGY = "weight"
USE_SUBJECT_THREADING_FOR_RT = True


def is_valid_user(email: str) -> bool:
    if not email:
        return False
    email = email.lower().strip()
    if "@" not in email:
        return False
    return email.split("@")[-1] in VALID_DOMAINS


def normalize_subject(subject: str) -> str:
    if not subject:
        return ""
    s = subject.strip().lower()
    while True:
        new_s = re.sub(r"^(re:|fw:|fwd:)\s*", "", s).strip()
        if new_s == s:
            break
        s = new_s
    return s


def process_data_pipeline(file_path: str, limit: int = 500000):
    print(f"[System] ETL started (limit={limit:,})")
    t0 = time.time()

    G = nx.DiGraph()
    user_stats = defaultdict(lambda: {
        "timestamps": [],
        "response_times": [],
        "in_degree_count": 0,
        "in_strength": 0,
        "out_strength": 0
    })

    pending_replies = defaultdict(deque)
    rows_kept = 0
    temporal_data = []

    re_subject = re.compile(r"Subject:\s*(.*)\n")
    re_from = re.compile(r"From:\s*([^\s]+@[^\s]+)")
    re_to = re.compile(r"To:\s*(.*?)\nSubject:", re.DOTALL)
    re_date = re.compile(r"Date:\s*(.*)\n")

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            next(reader, None)

            for count, row in enumerate(reader):
                if count >= limit or len(row) < 2:
                    continue

                raw_msg = row[1]

                m_sub = re_subject.search(raw_msg)
                subject = m_sub.group(1).strip() if m_sub else ""
                if any(s in subject.lower() for s in SKIP_SUBJECTS):
                    continue

                parts = raw_msg.split("\n\n", 1)
                body = parts[1] if len(parts) > 1 else ""
                if len(body.strip()) < MIN_BODY_LENGTH:
                    continue

                m_from = re_from.search(raw_msg)
                m_to = re_to.search(raw_msg)
                m_date = re_date.search(raw_msg)
                if not (m_from and m_to and m_date):
                    continue

                sender = m_from.group(1).strip().lower()
                if not is_valid_user(sender):
                    continue

                receivers = [
                    r.strip().lower()
                    for r in m_to.group(1).replace("\n", "").replace("\t", "").split(",")
                    if "@" in r and is_valid_user(r)
                ]
                if not receivers:
                    continue

                try:
                    dt = parser.parse(m_date.group(1).strip())
                except Exception:
                    continue

                temporal_data.append((sender, dt))
                user_stats[sender]["timestamps"].append(dt)

                thread_key = normalize_subject(subject) if USE_SUBJECT_THREADING_FOR_RT else ""

                for receiver in receivers:
                    if sender == receiver:
                        continue

                    if G.has_edge(sender, receiver):
                        G[sender][receiver]["weight"] += 1
                    else:
                        G.add_edge(sender, receiver, weight=1)

                    user_stats[receiver]["in_degree_count"] += 1
                    user_stats[receiver]["in_strength"] += 1
                    user_stats[sender]["out_strength"] += 1

                    key_reply = (receiver, sender, thread_key)
                    if pending_replies[key_reply]:
                        original_time = pending_replies[key_reply].popleft()
                        hours_diff = (dt - original_time).total_seconds() / 3600
                        if RT_MIN_HOURS < hours_diff < RT_MAX_HOURS:
                            user_stats[sender]["response_times"].append(hours_diff)

                    pending_replies[(sender, receiver, thread_key)].append(dt)

                rows_kept += 1
                if (count + 1) % 100000 == 0:
                    print(f"  processed {count+1:,} | kept {rows_kept:,}")

    except Exception as e:
        print(f"Error: {e}")
        return None, None, None, 0

    print(f"[ETL Done] kept={rows_kept:,} time={round(time.time()-t0,1)}s")
    print(f"[Network] nodes={G.number_of_nodes():,} edges={G.number_of_edges():,}")
    return G, user_stats, temporal_data, rows_kept


def pick_ei_for_typology(row: pd.Series) -> float:
    if EI_FOR_TYPOLOGY == "count":
        return float(row["EI_Count"]) if "EI_Count" in row else np.nan
    return float(row["EI_Weight"]) if "EI_Weight" in row else np.nan


def compute_fragmentation_impact(G: nx.DiGraph, members: list) -> float:
    if G.number_of_nodes() == 0:
        return 0.0

    original_lcc = len(max(nx.weakly_connected_components(G), key=len))
    G_tmp = G.copy()
    G_tmp.remove_nodes_from(members)
    new_lcc = len(max(nx.weakly_connected_components(G_tmp), key=len)) if G_tmp.number_of_nodes() > 0 else 0
    return round((original_lcc - new_lcc) / original_lcc * 100, 2) if original_lcc > 0 else 0.0


def assign_typology(row: pd.Series) -> str:
    avg_rt = row["Avg_Response_H"]
    ei = row["EI_Index"]

    is_slow = not pd.isna(avg_rt) and avg_rt > SLOW_THRESHOLD_H
    is_open = not pd.isna(ei) and ei > EI_OPEN_THRESHOLD

    if is_slow and not is_open:
        return "Black Hole"
    elif is_slow and is_open:
        return "Overloaded Hub"
    elif not is_slow and not is_open:
        return "Bureaucratic"
    else:
        return "Agile Connector"


def detect_communities_and_macro_table(G: nx.DiGraph, user_stats: dict, top_n: int = 10):
    Gu = G.to_undirected()
    comms = list(community.greedy_modularity_communities(Gu))
    Q = community.modularity(Gu, comms)
    print(f"[Macro] modularity={Q:.4f}")

    dept_members = {}
    user_dept_map = {}
    user_avg_rt = {u: np.mean(st["response_times"]) if st["response_times"] else np.nan for u, st in user_stats.items()}
    rows = []

    for i, members in enumerate(comms, start=1):
        members = list(members)
        if len(members) < 10:
            continue

        key_user = max(members, key=lambda x: user_stats[x]["in_degree_count"])
        dept_id = f"C{i}_{key_user.split('@')[0]}"

        dept_members[dept_id] = members
        for m in members:
            user_dept_map[m] = dept_id

        internal_c = external_c = internal_w = external_w = 0
        member_set = set(members)

        for m in members:
            for n in G.successors(m):
                w = G[m][n].get("weight", 1)
                if n in member_set:
                    internal_c += 1
                    internal_w += w
                else:
                    external_c += 1
                    external_w += w

        total_c = internal_c + external_c
        total_w = internal_w + external_w

        ei_c = (external_c - internal_c) / total_c if total_c > 0 else 0.0
        ei_w = (external_w - internal_w) / total_w if total_w > 0 else 0.0

        rts = [user_avg_rt[u] for u in members if not np.isnan(user_avg_rt[u])]
        avg_rt = np.mean(rts) if rts else np.nan

        bn_cnt = sum(1 for u in members if not np.isnan(user_avg_rt[u]) and user_avg_rt[u] > SLOW_THRESHOLD_H)
        loads = sorted([user_stats[u]["in_degree_count"] for u in members], reverse=True)
        total_load = sum(loads)
        top_k = max(1, int(len(members) * 0.1))
        skew = sum(loads[:top_k]) / total_load * 100 if total_load > 0 else np.nan

        row = {
            "Dept_ID": dept_id,
            "Size": len(members),
            "Avg_Response_H": round(avg_rt, 2) if not np.isnan(avg_rt) else np.nan,
            "Bottleneck_Density_%": round(bn_cnt / len(members) * 100, 1),
            "Workload_Skew_%": round(skew, 1) if not np.isnan(skew) else np.nan,
            "EI_Count": round(ei_c, 2),
            "EI_Weight": round(ei_w, 2),
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    df["Frag_Impact_%"] = df["Dept_ID"].apply(lambda d: compute_fragmentation_impact(G, dept_members[d]))
    df["EI_Index"] = df.apply(pick_ei_for_typology, axis=1)
    df["Typology"] = df.apply(assign_typology, axis=1)

    df = df.sort_values("Frag_Impact_%", ascending=False)
    print(df.head(top_n).to_string(index=False))
    return df, dept_members, user_dept_map, Q


def build_individual_table(G: nx.DiGraph, user_stats: dict, user_dept_map: dict):
    rows = []
    for u in G.nodes():
        st = user_stats[u]
        avg_rt = np.mean(st["response_times"]) if st["response_times"] else np.nan

        ext_w = tot_w = 0
        for v in G.successors(u):
            w = G[u][v]["weight"]
            tot_w += w
            if user_dept_map.get(v) != user_dept_map.get(u):
                ext_w += w

        rows.append({
            "User": u,
            "Dept_ID": user_dept_map.get(u, "Unknown"),
            "Received_Count": st["in_degree_count"],
            "Avg_Response_H": round(avg_rt, 2) if not np.isnan(avg_rt) else np.nan,
            "External_Out_%": round(ext_w / tot_w * 100, 1) if tot_w > 0 else 0.0
        })

    return pd.DataFrame(rows).set_index("User")


def add_betweenness_for_micro(indiv_df: pd.DataFrame, G: nx.DiGraph):
    btw = nx.betweenness_centrality(G, k=BETWEENNESS_SAMPLE_K)
    indiv_df["Betweenness"] = [btw.get(u, 0.0) for u in indiv_df.index]
    return indiv_df


def visualize_top10(cluster_df: pd.DataFrame):
    if cluster_df.empty:
        return

    top_df = cluster_df.head(10)
    plt.figure(figsize=(12, 8))
    scatter = plt.scatter(
        top_df["EI_Index"],
        top_df["Avg_Response_H"],
        s=top_df["Size"] * 5,
        c=top_df["Frag_Impact_%"],
        cmap="viridis",
        alpha=0.85,
        edgecolors="k"
    )
    plt.axvline(EI_OPEN_THRESHOLD, linestyle="--", color="gray")
    plt.axhline(SLOW_THRESHOLD_H, linestyle="--", color="red")
    plt.colorbar(scatter, label="Frag Impact (%)")
    plt.xlabel("Openness (E–I Index)")
    plt.ylabel("Avg Response Time (hours)")
    plt.title("Top10 Organizational Dynamics Map")
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.show()


def run_micro_simulation(G: nx.DiGraph, dept_id: str, members: list, user_stats: dict, top_k: int = 5):
    if not members:
        return

    sorted_by_load = sorted(members, key=lambda x: user_stats[x]["in_degree_count"], reverse=True)[:top_k]

    orig_lcc = len(max(nx.weakly_connected_components(G), key=len))

    G_load = G.copy()
    G_load.remove_nodes_from(sorted_by_load)
    new_lcc_load = len(max(nx.weakly_connected_components(G_load), key=len))
    loss_load = (orig_lcc - new_lcc_load) / orig_lcc * 100

    btw_subset = nx.betweenness_centrality_subset(G, sources=members, targets=list(G.nodes()), normalized=True)
    sorted_by_conn = sorted(members, key=lambda x: btw_subset.get(x, 0), reverse=True)[:top_k]

    G_conn = G.copy()
    G_conn.remove_nodes_from(sorted_by_conn)
    new_lcc_conn = len(max(nx.weakly_connected_components(G_conn), key=len))
    loss_conn = (orig_lcc - new_lcc_conn) / orig_lcc * 100

    print(f"\n[Micro Simulation] {dept_id} (Removing Top {top_k})")
    print(f"   - Remove Load Absorbers: LCC Loss = {loss_load:.2f}%")
    print(f"   - Remove Connectors:     LCC Loss = {loss_conn:.2f}%")
    if loss_load > 0:
        multiplier = loss_conn / loss_load
        print(f"   => Connector Impact is {multiplier:.1f}x greater than Volume Impact")


def visualize_dept_micro(indiv_df: pd.DataFrame, dept_id: str, typology: str):
    dept_df = indiv_df[indiv_df["Dept_ID"] == dept_id].copy()

    if dept_df.empty:
        print(f"No members found for {dept_id}")
        return

    plt.figure(figsize=(10, 7))

    x = dept_df["Received_Count"]
    y = dept_df["Betweenness"]

    sc = plt.scatter(
        x, y,
        c=dept_df["Avg_Response_H"],
        cmap="coolwarm_r",
        s=100,
        edgecolors="k",
        alpha=0.8
    )

    plt.colorbar(sc, label="Avg Response Time (Hours)")
    plt.title(f"Micro View: {dept_id} ({typology})", fontsize=14)
    plt.xlabel("Workload Volume (In-Degree)", fontsize=12)
    plt.ylabel("Structural Influence (Betweenness)", fontsize=12)
    plt.grid(True, linestyle="--", alpha=0.5)

    top_connectors = dept_df.nlargest(3, "Betweenness")
    for idx, row in top_connectors.iterrows():
        plt.text(row["Received_Count"], row["Betweenness"],
                 idx.split("@")[0], fontsize=9, fontweight='bold', ha='right')

    plt.tight_layout()
    plt.show()


if __name__ == "__main__":
    file_path = "/Users/sean/Downloads/emails.csv"
    TOP_N = 10

    G, user_stats, temporal_data, kept = process_data_pipeline(file_path)
    if G is None or kept == 0:
        sys.exit(0)

    cluster_df, dept_members, user_dept_map, Q = detect_communities_and_macro_table(G, user_stats, TOP_N)

    indiv_df = build_individual_table(G, user_stats, user_dept_map)
    indiv_df = add_betweenness_for_micro(indiv_df, G)

    print("\n[Visualizing Macro View...]")
    visualize_top10(cluster_df)

    if not cluster_df.empty:
        target_dept = cluster_df.iloc[0]
        dept_id = target_dept["Dept_ID"]
        typology = target_dept["Typology"]

        print(f"\n[Drill Down Analysis] Target: {dept_id} ({typology})")

        members = dept_members[dept_id]
        run_micro_simulation(G, dept_id, members, user_stats, top_k=10)

        print(f"Visualizing Micro View for {dept_id}...")
        visualize_dept_micro(indiv_df, dept_id, typology)
