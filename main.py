import yfinance as yf
import pandas as pd
import numpy as np
import requests
import os
import datetime
import time

# ================= ⚙️ 用户配置 =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
CSV_FILE = "gold_record.csv"  # 记账本文件名
TARGET_SYMBOL = "GLD"         # 预测标的 (黄金ETF)
VERIFY_SYMBOL = "GLD"         # 验证标的 (直接用GLD验证)

# ================= 🛠️ 功能函数 =================

def get_market_data():
    """获取当前的 ATM IV 和价格，计算预测范围"""
    try:
        ticker = yf.Ticker(TARGET_SYMBOL)
        # 获取实时价格 (fast_info 通常更及时)
        price = ticker.fast_info['last_price']
        
        # 获取期权链
        options = ticker.options
        if not options: return None
        
        # 选取最近到期日 (捕捉短期情绪)
        chain = ticker.option_chain(options[0])
        calls = chain.calls
        
        # 寻找平值 (ATM) IV
        atm_idx = (np.abs(calls['strike'] - price)).argmin()
        iv = calls.iloc[atm_idx]['impliedVolatility']
        
        # 计算预期波动 (Rule of 16)
        expected_move = price * (iv / 16)
        
        return {
            "price": price,
            "iv": iv,
            "low": price - expected_move,
            "high": price + expected_move
        }
    except Exception as e:
        print(f"数据获取失败: {e}")
        return None

def verify_history(df):
    """验证过去未出结果的记录 (昨天及以前)"""
    # 筛选出Result为空，且不是今天的记录
    today_str = str(datetime.date.today())
    # 找出所有 result 列是空值 (NaN) 的行索引
    pending_indices = df[df['result'].isna()].index
    
    updates = 0
    try:
        # 获取最近5天历史数据用于比对
        hist = yf.Ticker(VERIFY_SYMBOL).history(period="5d")
        hist.index = hist.index.strftime('%Y-%m-%d')
        
        for idx in pending_indices:
            record_date = df.at[idx, 'date']
            
            # 如果这一行是今天的，跳过（因为今天还没收盘，无法验证）
            if record_date == today_str:
                continue
                
            if record_date in hist.index:
                # 获取当天的实际最高/最低
                day_data = hist.loc[record_date]
                act_high = day_data['High']
                act_low = day_data['Low']
                
                # 读取当时的预测
                pred_high = df.at[idx, 'high_pred']
                pred_low = df.at[idx, 'low_pred']
                
                # 判定逻辑：实际价格在预测范围内算 WIN (震荡策略)
                is_win = (act_high <= pred_high) and (act_low >= pred_low)
                
                # 更新表格
                df.at[idx, 'actual_high'] = act_high
                df.at[idx, 'actual_low'] = act_low
                df.at[idx, 'result'] = "WIN" if is_win else "LOSS"
                updates += 1
                
    except Exception as e:
        print(f"验证历史出错: {e}")
        
    return updates

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("未配置 Telegram Token，跳过发送")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"发送失败: {e}")

# ================= 🚀 主程序 =================

def main():
    print("开始运行...")
    
    # 1. 读取或初始化 CSV
    if os.path.exists(CSV_FILE):
        df = pd.read_csv(CSV_FILE)
    else:
        cols = ['date', 'price', 'iv', 'low_pred', 'high_pred', 'actual_high', 'actual_low', 'result']
        df = pd.DataFrame(columns=cols)

    # 2. 验证历史记录
    verify_count = verify_history(df)
    
    # 计算胜率
    completed = df[df['result'].notna()]
    wins = completed[completed['result'] == 'WIN'].shape[0]
    total = completed.shape[0]
    win_rate = (wins / total) if total > 0 else 0.0
    
    history_msg = f"📊 **历史胜率**: {win_rate:.1%} ({wins}/{total})"
    if verify_count > 0:
        history_msg += f" (刚验证了 {verify_count} 单)"

    # 3. 获取今日数据 & 预测
    today_str = str(datetime.date.today())
    data = get_market_data()
    
    today_msg = ""
    run_status = ""
    
    if data:
        # 检查 CSV 里是否已经有今天的日期
        existing_today = df.index[df['date'] == today_str].tolist()
        
        if existing_today:
            # --- 晚盘逻辑：如果今天已经有记录，就更新它 (Overwrite) ---
            idx = existing_today[0]
            df.at[idx, 'price'] = data['price']
            df.at[idx, 'iv'] = data['iv']
            df.at[idx, 'low_pred'] = data['low']
            df.at[idx, 'high_pred'] = data['high']
            run_status = "🔄 **晚盘更新 (美股开盘)**"
        else:
            # --- 早盘逻辑：如果没有记录，就新建一行 (Append) ---
            new_row = {
                'date': today_str,
                'price': data['price'],
                'iv': data['iv'],
                'low_pred': data['low'],
                'high_pred': data['high'],
                'actual_high': None, 'actual_low': None, 'result': None
            }
            # 使用 pd.concat 替代 append
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            run_status = "☀️ **早盘计划 (亚盘时段)**"
            
        today_msg = (
            f"{run_status}\n"
            f"📅 日期: {today_str}\n"
            f"💰 标的: {TARGET_SYMBOL} (IV: {data['iv']:.1%})\n"
            f"📉 下限: `{data['low']:.2f}`\n"
            f"📈 上限: `{data['high']:.2f}`"
        )
    else:
        today_msg = "⚠️ 无法获取今日数据 (可能是休市或网络问题)"

    # 4. 保存 CSV 文件
    df.to_csv(CSV_FILE, index=False)
    print("CSV 文件已保存")

    # 5. 发送 Telegram
    final_report = f"{today_msg}\n\n------------------\n{history_msg}"
    send_telegram(final_report)
    print("消息已推送")

if __name__ == "__main__":
    main()
