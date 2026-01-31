"""
IMPORTANT ETHICAL NOTE:
This analysis identifies structural and behavioral signals in communication
networks. Metrics are diagnostic indicators only and should not be used
to label individual performance or intent.
"""

import csv
import re
import networkx as nx
from textblob import TextBlob
from collections import defaultdict
import numpy as np

# ----------------------------------------------------------------------
# 1. 데이터 로드 및 감성/네트워크 분석 준비
# ----------------------------------------------------------------------
def analyze_enron_deep(file_path, limit=3000):
    # 방향성 있는 그래프 (DiGraph) 생성
    G = nx.DiGraph()
    
    # 데이터 저장소
    user_sentiments = defaultdict(list) # 유저가 보낸 메일들의 감정 점수 리스트
    after_hours_count = defaultdict(int) # 업무 시간 외 메일 카운트
    
    print(f"📂 '{file_path}' 심화 분석 시작 (최대 {limit}건)...")
    
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.reader(f)
        next(reader) # 헤더 건너뛰기
        
        count = 0
        for row in reader:
            if count >= limit: break
            
            raw_message = row[1]
            
            # [파싱] 보낸사람, 받는사람, 날짜, 본문 추출
            from_match = re.search(r"From: ([\w\.-]+@[\w\.-]+)", raw_message)
            to_match = re.search(r"To: (.*?)\nSubject:", raw_message, re.DOTALL)
            date_match = re.search(r"Date: .*? (\d{2}):\d{2}:\d{2}", raw_message) # 시간 추출
            
            # 본문 추출 (헤더 이후 내용) - 감성 분석용
            body_match = re.search(r"\n\n(.*)", raw_message, re.DOTALL)
            
            if from_match and to_match:
                sender = from_match.group(1).strip()
                receivers = [r.strip() for r in to_match.group(1).split(',') if '@' in r]
                
                # [감성 분석] -1(매우 부정) ~ +1(매우 긍정)
                sentiment_score = 0
                if body_match:
                    body_text = body_match.group(1)
                    blob = TextBlob(body_text)
                    sentiment_score = blob.sentiment.polarity
                    user_sentiments[sender].append(sentiment_score)

                # [시간 분석] 워라밸 파괴자 찾기 (오후 7시 ~ 오전 6시 사이 발송)
                if date_match:
                    hour = int(date_match.group(1))
                    if hour >= 19 or hour < 6:
                        after_hours_count[sender] += 1

                # [네트워크 구축]
                for receiver in receivers:
                    receiver = receiver.strip()
                    # 엣지에 '감정 점수'를 속성으로 추가
                    G.add_edge(sender, receiver, sentiment=sentiment_score)
                
                count += 1
                if count % 500 == 0: print(f"  - {count}건 처리 중...")

    return G, user_sentiments, after_hours_count

# ----------------------------------------------------------------------
# 2. HR 인사이트 도출 로직
# ----------------------------------------------------------------------
def generate_hr_insights(G, user_sentiments, after_hours_count):
    print("\n" + "="*60)
    print("📋 HR Analyst 리더십 & 조직문화 진단 리포트")
    print("="*60)

    # 1. [Toxic Influencer] 영향력은 큰데, 평균 언어가 부정적인 사람
    # PageRank(영향력) 계산
    pagerank = nx.pagerank(G)
    
    toxic_candidates = []
    for user, scores in user_sentiments.items():
        if len(scores) > 5: # 최소 5통 이상 보낸 사람만
            avg_sentiment = np.mean(scores)
            influence = pagerank.get(user, 0)
            # 조건: 영향력 상위권이면서, 감정이 부정적(< 0)인 사람
            if avg_sentiment < 0: 
                toxic_candidates.append((user, avg_sentiment, influence))
    
    # 영향력 * 부정적강도 로 정렬
    toxic_candidates.sort(key=lambda x: x[1] * x[2]) # (음수 * 양수 = 더 작은 음수가 1등)
    
    print("\n🤬 [Toxic Influencer] 부정적 언어를 전파하는 영향력자 (Top 3)")
    if toxic_candidates:
        for i, (user, sent, inf) in enumerate(toxic_candidates[:3]):
            print(f"  {i+1}. {user} (감정: {sent:.2f}, 영향력: {inf:.4f})")
            print(f"     -> 해석: 조직 내 부정적 기류를 형성할 위험이 큼.")
    else:
        print("  - 뚜렷한 Toxic Influencer가 발견되지 않았습니다.")

    # 2. [Passive Leader / Bottleneck] 정보 병목 현상
    # In-Degree(수신)는 높은데 Out-Degree(발신)가 낮은 사람
    bottlenecks = []
    for user in G.nodes():
        in_d = G.in_degree(user)
        out_d = G.out_degree(user)
        if in_d > 10: # 충분히 메일을 받는 사람 중에서
            ratio = out_d / (in_d + 1)
            if ratio < 0.1: # 받은 것에 비해 보낸 게 10% 미만
                bottlenecks.append((user, in_d, out_d))
    
    bottlenecks.sort(key=lambda x: x[1], reverse=True)
    
    print("\n🕳️ [Passive Leader] 정보 블랙홀/병목 의심자 (Top 3)")
    for i, (user, in_d, out_d) in enumerate(bottlenecks[:3]):
        print(f"  {i+1}. {user} (수신: {in_d}, 발신: {out_d})")
        print(f"     -> 해석: 의사결정이 지연되거나, 팀원들이 답답해할 수 있음.")

    # 3. [Misbehavior] 워라밸 파괴자 (After-hours emailing)
    sorted_workaholics = sorted(after_hours_count.items(), key=lambda x: x[1], reverse=True)
    print("\n🌙 [Misbehavior] 업무 시간 외 이메일 과다 발송자 (Top 3)")
    for i, (user, count) in enumerate(sorted_workaholics[:3]):
        print(f"  {i+1}. {user} ({count}건 발송)")
        print(f"     -> 해석: 본인의 번아웃 위험 + 팀원에게 상시 연결 압박을 줄 수 있음.")

    # 4. [Silo] 커뮤니티 감지 (간단 버전)
    # 연결이 끊어진 독립된 그룹(Component)이 있는지 확인
    components = list(nx.weakly_connected_components(G))
    print(f"\n🧩 [Silo 진단] 조직 파편화 정도")
    print(f"  - 전체 네트워크가 {len(components)}개의 독립된 그룹으로 쪼개져 있습니다.")
    if len(components) > 1:
        print(f"     -> 해석: {len(components)}개의 그룹이 서로 소통하지 않고 단절되어 있습니다(Silo).")
        print(f"     -> 가장 큰 그룹 크기: {len(components[0])}명, 두 번째 그룹: {len(components[1])}명")
    else:
        print("     -> 해석: 전체 조직이 하나로 잘 연결되어 있습니다.")

# ----------------------------------------------------------------------
# 3. 실행
# ----------------------------------------------------------------------
if __name__ == "__main__":
    file_path = "data/emails.csv" # 다운로드 받은 파일 경로
    try:
        G, user_sentiments, after_hours_count = analyze_enron_deep(file_path, limit=5000)
        generate_hr_insights(G, user_sentiments, after_hours_count)
    except FileNotFoundError:
        print("파일을 찾을 수 없습니다. 'emails.csv' 경로를 확인해주세요.")
