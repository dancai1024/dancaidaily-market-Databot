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
CSV_FILE = "market_record.csv"  # 改个名字，代表全市场

# 定义要回测的所有品种
# symbol: 用于获取期权链和预测的 ETF
# verify_ticker: 用于验证结果的标的 (通常就是 ETF 本身，数据最准)
ASSETS = [
    {"name": "🏆 黄金", "symbol": "GLD", "verify_ticker": "GLD"},
    {"name": "🛢️ 原油", "symbol": "USO", "verify_ticker": "USO"},
    {"name": "🔥 天然气", "symbol": "UNG", "verify_ticker": "UNG"},
    {"name": "📈 标普500", "symbol": "SPY", "verify_ticker": "SPY"},
    {"name": "💻 纳指100", "symbol": "QQQ", "verify_ticker": "QQQ"},
    {"name": "🏭 道琼斯", "symbol": "DIA", "verify_ticker": "DIA"},
]

# ================= 🛠️ 功能函数 =================

def get_prediction(asset):
    """获取单个品种的预测数据"""
    symbol = asset['symbol']
    try:
        ticker = yf.Ticker(symbol)
        price = ticker.fast_info['last_price']
        
        options = ticker.options
        if not options: return None
        
        # 选取最近到期日
        chain = ticker.option_chain(options[0])
        calls = chain.calls
        
        # 寻找 ATM IV
        atm_idx = (np.abs(calls['strike'] - price)).argmin()
        iv = calls.iloc[atm_idx]['impliedVolatility']
        
        # 计算预期波动 (Rule of 16)
        move = price * (iv / 16)
        
        return {
            "name": asset['name'],
            "symbol": symbol,
            "price": price,
            "iv": iv,
            "low": price - move,
            "high": price + move
        }
    except Exception as e:
        print(f"❌ {asset['name']} 获取失败: {e}")
        return None

def verify_history(df):
    """验证 CSV 中所有未结算是的单子"""
    updates_count = 0
    today_str = str(datetime.date.today())
    
    # 筛选出 result 为空，且日期不是今天的记录
    pending_mask = (df['result'].isna()) & (df['date'] != today_str)
    pending_indices = df[pending_mask].index
    
    if len(pending_indices) == 0:
        return 0

    print(f"正在验证 {len(pending_indices)} 条历史记录...")

    # 为了效率，我们按品种分组验证
    for asset in ASSETS:
        symbol = asset['symbol']
        verify_ticker = asset['verify_ticker']
        
        # 找到属于该品种的待验证行
        # 注意：这里要确保 CSV 里的 symbol 和 ASSETS 里的 symbol 一致
        asset_indices = df[pending_mask & (df['symbol'] == symbol)].index
        
        if len(asset_indices) > 0:
            try:
                # 拉取该品种最近 5 天历史
                hist = yf.Ticker(verify_ticker).history(period="5d")
                hist.index = hist.index.strftime('%Y-%m-%d')
                
                for idx in asset_indices:
                    record_date = df.at[idx, 'date']
                    
                    if record_date in hist.index:
                        day_data = hist.loc[record_date]
                        act_high = day_data['High']
                        act_low = day_data['Low']
                        
                        pred_high = df.at[idx, 'high_pred']
                        pred_low = df.at[idx, 'low_pred']
                        
                        # 判定逻辑：未突破预测范围算 WIN (震荡策略)
                        is_win = (act_high <= pred_high) and (act_low >= pred_low)
                        
                        df.at[idx, 'actual_high'] = act_high
                        df.at[idx, 'actual_low'] = act_low
                        df.at[idx, 'result'] = "WIN" if is_win else "LOSS"
                        updates_count += 1
            except Exception as e:
                print(f"验证 {symbol} 出错: {e}")
                
    return updates_count

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    requests.post(url, json=payload)

# ================= 🚀 主程序 =================

def main():
    # 1. 读取或初始化 CSV (增加了 symbol 列)
    cols = ['date', 'symbol', 'name', 'price', 'iv', 'low_pred', 'high_pred', 'actual_high', 'actual_low', 'result']
    
    if os.path.exists(CSV_FILE):
        try:
            df = pd.read_csv(CSV_FILE)
            # 简单的兼容性检查：如果旧文件没有 symbol 列，重建
            if 'symbol' not in df.columns:
                print("旧格式 CSV，重建文件...")
                df = pd.DataFrame(columns=cols)
        except:
            df = pd.DataFrame(columns=cols)
    else:
        df = pd.DataFrame(columns=cols)

    # 2. 验证历史
    verify_updates = verify_history(df)
    
    # 3. 统计胜率 (总胜率)
    completed = df[df['result'].notna()]
    wins = completed[completed['result'] == 'WIN'].shape[0]
    total = completed.shape[0]
    win_rate = (wins / total) if total > 0 else 0.0
    
    history_report = f"📊 **总胜率**: {win_rate:.1%} ({wins}/{total}单)"
    if verify_updates > 0:
        history_report += f" (更新了 {verify_updates} 单)"

    # 4. 循环获取今日预测
    today_str = str(datetime.date.today())
    today_lines = []
    run_type = "☀️ 早盘"
    
    print("开始获取今日数据...")
    for asset in ASSETS:
        data = get_prediction(asset)
        if not data:
            today_lines.append(f"⚠️ {asset['name']}: 失败")
            continue
            
        # 检查该品种今天是否已存在记录
        # 使用 date 和 symbol 双重定位
        mask = (df['date'] == today_str) & (df['symbol'] == data['symbol'])
        existing_idx = df[mask].index
        
        if len(existing_idx) > 0:
            # --- 更新逻辑 (晚盘) ---
            run_type = "🔄 晚盘更新"
            idx = existing_idx[0]
            df.at[idx, 'price'] = data['price']
            df.at[idx, 'iv'] = data['iv']
            df.at[idx, 'low_pred'] = data['low']
            df.at[idx, 'high_pred'] = data['high']
        else:
            # --- 新建逻辑 (早盘) ---
            new_row = {
                'date': today_str,
                'symbol': data['symbol'],
                'name': data['name'],
                'price': data['price'],
                'iv': data['iv'],
                'low_pred': data['low'],
                'high_pred': data['high'],
                'actual_high': None, 'actual_low': None, 'result': None
            }
            # 使用 concat
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        
        # 格式化输出一行
        line = f"*{data['name']}*: `{data['low']:.2f}` ~ `{data['high']:.2f}` (IV:{data['iv']:.0%})"
        today_lines.append(line)

    # 5. 保存 & 发送
    df.to_csv(CSV_FILE, index=False)
    
    today_msg = "\n".join(today_lines)
    final_msg = (
        f"{run_type}计划 ({today_str})\n"
        f"------------------\n"
        f"{today_msg}\n\n"
        f"{history_report}"
    )
    
    send_telegram(final_msg)
    print("完成！")

if __name__ == "__main__":
    main()
