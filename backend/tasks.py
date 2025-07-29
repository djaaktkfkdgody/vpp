from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import pymysql

def get_connection():
    return pymysql.connect(
        host="database-1.cts2qeeg0ot5.ap-northeast-2.rds.amazonaws.com",
        user="kevin",
        db="vpp_2",
        password="spreatics*",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )

# datetime.now()가 15분으로 정확히 찍히지 않을 경우 예방하기 위한 15분단위로 반올림 해주는 함수 
def round_to_nearest_15min(dt):
    discard = timedelta(minutes=dt.minute % 15,
                        seconds=dt.second,
                        microseconds=dt.microsecond)
    dt -= discard
    if discard >= timedelta(minutes=7.5):
        dt += timedelta(minutes=15)
    return dt

#입찰 결과 결정 및 bidding_result 반영
def evaluate_bids():
    print(f"[{datetime.now()}] ⏳ 입찰 평가 시작")
    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            # 최신 입찰 배치 ID 조회
            cursor.execute("SELECT MAX(bid_id) AS latest_bid_id FROM bidding_log")
            row = cursor.fetchone()
            latest_bid_id = row["latest_bid_id"]

            if not latest_bid_id:
                print("🚫 평가할 입찰 없음")
                return

            # 이미 평가된 적 있는지 확인
            cursor.execute("""
                SELECT COUNT(*) AS cnt FROM bidding_result WHERE bid_id = %s
            """, (latest_bid_id,))
            if cursor.fetchone()["cnt"] > 0:
                print(f"⚠️ 이미 평가된 입찰 batch {latest_bid_id}, 생략")
                return
            
            rounded_time = round_to_nearest_15min(datetime.now())

            # 해당 배치의 입찰 정보 가져오기
            cursor.execute("""
                SELECT * FROM bidding_log WHERE bid_id = %s
            """, (latest_bid_id,))
            bids = cursor.fetchall()

            # 현재 SMP 단가 조회 (가장 가까운 값 사용)
            cursor.execute("""
                SELECT price_per_kwh
                FROM smp_data
                WHERE smp_time = %s
                ORDER BY timestamp DESC
                LIMIT 1
            """, (rounded_time,))
            smp_row = cursor.fetchone()
            if not smp_row:
                print("❌ SMP 데이터 없음")
                return

            market_price = smp_row["price_per_kwh"]

            # 평가 및 결과 저장
            for bid in bids:
                result = 'accepted' if bid["bid_price_per_kwh"] <= market_price else 'rejected'

                cursor.execute("""
                    INSERT INTO bidding_result (bid_id, entity_id, quantity_kwh, bid_price, result)
                    VALUES (%s, %s, %s, %s, %s)
                """, (
                    latest_bid_id,
                    bid["entity_id"],
                    bid["bid_quantity_kwh"],
                    bid["bid_price_per_kwh"],
                    result
                ))

            conn.commit()
            print(f"✅ 입찰 평가 완료: batch {latest_bid_id} (SMP {market_price})")

    except Exception as e:
        print("❌ 에러 발생:", str(e))
    finally:
        conn.close()



#수익 계산 
def calculate_profit():
    now = datetime.now().replace(microsecond=0)
    print(f"[{now}] 💰 수익 계산 중...")

    try:
        conn = get_connection()
        with conn.cursor() as cursor:
            # 1. 최신 accepted 입찰 전체 조회 (유효한 입찰)
            cursor.execute("""
                SELECT br.entity_id, br.bid_price
                FROM bidding_result br
                JOIN (
                    SELECT entity_id, MAX(id) as max_id
                    FROM bidding_result
                    WHERE result = 'accepted'
                    GROUP BY entity_id
                ) latest ON br.id = latest.max_id
            """)
            accepted_bids = cursor.fetchall()

            for bid in accepted_bids:
                entity_id = bid["entity_id"]
                unit_price = bid["bid_price"]

                # 2. 발전소 최신 발전량 조회
                cursor.execute("""
                    SELECT power_kw
                    FROM node_status_log
                    WHERE relay_id = %s
                    ORDER BY node_timestamp DESC
                    LIMIT 1
                """, (entity_id,))
                power_row = cursor.fetchone()

                if power_row:
                    power_kw = power_row["power_kw"]
                    # 20초 동안 발전량 단위 변환 수익 계산
                    revenue = round(power_kw * unit_price * (20.0 / 3600), 2)

                    # 3. profit_log에 누적 기록 (중복 가능)
                    cursor.execute("""
                        INSERT INTO profit_log (timestamp, entity_id, unit_price, revenue_krw)
                        VALUES (%s, %s, %s, %s)
                    """, (now, entity_id, unit_price, revenue))

        conn.commit()
        conn.close()
        print(f"[{now}] ✅ 수익 저장 완료")

    except Exception as e:
        print(f"❌ calculate_profit 오류: {e}")






# 스케줄러 설정
def start_scheduler():
    scheduler = BackgroundScheduler(timezone='Asia/Seoul')
    scheduler.add_job(evaluate_bids, 'cron', minute='*/15')  # 매 15분마다 실행
    scheduler.add_job(calculate_profit, 'interval', seconds=20)    # 20초마다 수익 계산
    scheduler.start()
    print("📅 APScheduler 시작됨 (15분 간격)")


# 메인 진입점
if __name__ == "__main__":
    start_scheduler()
    # 앱이 종료되지 않도록 유지
    try:
        while True:
            pass
    except (KeyboardInterrupt, SystemExit):
        print("🛑 종료됨")
