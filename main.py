import os  # 新增这一行
import yfinance as yf
import pandas as pd
import numpy as np
import requests
import datetime
import time

# ================= 🔧 用户配置区域 =================
# 修改这两行，不再直接填字符串，而是从环境变量读取
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# ================= 📝 资产配置清单 =================
# 结构说明: 
# name: 显示名称
# spot: 期货/现货代码 (用于获取你真正关心的价格, GC=F 是黄金期货, ^GSPC 是标普指数)
# etf:  ETF代码 (用于反推期权链 IV)
# vol_index: 波动率指数代码 (用于方案B, 如果没有填 None)

ASSETS = [
    {
        "name": "🏆 黄金 (Gold)", 
        "spot": "GC=F",       # COMEX 黄金期货
        "etf": "GLD", 
        "vol_index": "^GVZ"   # CBOE 黄金波动率指数
    },
    {
        "name": "🛢️ 原油 (Crude Oil)", 
        "spot": "CL=F",       # NYMEX 原油期货
        "etf": "USO", 
        "vol_index": "^OVX"   # CBOE 石油波动率指数
    },
    {
        "name": "🔥 天然气 (Nat Gas)", 
        "spot": "NG=F",       # 天然气期货
        "etf": "UNG", 
        "vol_index": None     # 天然气通常没有免费实时的波动率指数，用 None 跳过
    },
    {
        "name": "🇺🇸 标普500 (S&P 500)", 
        "spot": "^GSPC",      # 标普500指数
        "etf": "SPY", 
        "vol_index": "^VIX"   # 著名的恐慌指数
    },
    {
        "name": "💻 纳斯达克 (Nasdaq)", 
        "spot": "^IXIC",      # 纳指
        "etf": "QQQ", 
        "vol_index": "^VXN"   # 纳指波动率
    },
    {
        "name": "🏭 道琼斯 (Dow Jones)", 
        "spot": "^DJI",       # 道指
        "etf": "DIA", 
        "vol_index": "^VXD"   # 道指波动率
    }
]

# ================= 🧮 核心计算函数 =================

def get_market_data(asset):
    result = {
        "name": asset['name'],
        "price": 0.0,
        "method_a": {"iv": 0.0, "move": 0.0, "low": 0.0, "high": 0.0},
        "method_b": {"iv": 0.0, "move": 0.0, "low": 0.0, "high": 0.0, "valid": False}
    }
    
    try:
        # 1. 获取标的价格 (优先用期货/指数价格，因为这是你要操作的标的)
        spot_ticker = yf.Ticker(asset['spot'])
        try:
            current_price = spot_ticker.fast_info['last_price']
        except:
            # 如果 fast_info 失败，尝试 history
            hist = spot_ticker.history(period="1d")
            if not hist.empty:
                current_price = hist['Close'].iloc[-1]
            else:
                return None # 拿不到价格无法计算
        
        result['price'] = current_price

        # -------------------------------------------
        # 方案 A: 通过 ETF 期权链反推 (Option Implied Vol)
        # -------------------------------------------
        etf_ticker = yf.Ticker(asset['etf'])
        try:
            options = etf_ticker.options
            if options:
                # 找最近的到期日
                chain = etf_ticker.option_chain(options[0])
                # 获取 ETF 当前价用于定位 ATM
                etf_price = etf_ticker.fast_info['last_price']
                
                # 寻找平值 (ATM) Call
                calls = chain.calls
                atm_idx = (np.abs(calls['strike'] - etf_price)).argmin()
                atm_iv = calls.iloc[atm_idx]['impliedVolatility']
                
                # 计算
                # Rule of 16: 日波动 = 年化IV / 16
                daily_move_pct = atm_iv / 16
                move_value = current_price * daily_move_pct
                
                result['method_a'] = {
                    "iv": atm_iv,
                    "move": move_value,
                    "low": current_price - move_value,
                    "high": current_price + move_value
                }
        except Exception as e:
            print(f"方案A计算失败 {asset['name']}: {e}")

        # -------------------------------------------
        # 方案 B: 直接读取波动率指数 (Vol Index)
        # -------------------------------------------
        if asset['vol_index']:
            try:
                vix_ticker = yf.Ticker(asset['vol_index'])
                vix_val = vix_ticker.fast_info['last_price']
                
                # VIX 20 代表年化波动率 20% -> 0.20
                idx_iv = vix_val / 100
                daily_move_pct = idx_iv / 16
                move_value = current_price * daily_move_pct
                
                result['method_b'] = {
                    "iv": idx_iv,
                    "move": move_value,
                    "low": current_price - move_value,
                    "high": current_price + move_value,
                    "valid": True
                }
            except Exception as e:
                print(f"方案B计算失败 {asset['name']}: {e}")
                
        return result

    except Exception as e:
        print(f"整体获取失败 {asset['name']}: {e}")
        return None

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=data)
    except Exception as e:
        print(f"发送失败: {e}")

# ================= 🚀 主程序 =================

def main():
    print("正在计算数据，请稍候...")
    msg_lines = [f"📊 **全市场波动率日报** ({datetime.date.today()})", ""]
    
    for asset in ASSETS:
        data = get_market_data(asset)
        if not data:
            msg_lines.append(f"❌ {asset['name']}: 数据获取失败")
            continue
            
        # 格式化输出
        msg_lines.append(f"➖➖➖➖➖➖➖➖➖➖")
        msg_lines.append(f"*{data['name']}*")
        msg_lines.append(f"💰 标的现价: `{data['price']:,.2f}`")
        
        # 方案 A 输出
        a = data['method_a']
        if a['iv'] > 0:
            msg_lines.append(f"🔹 **方案A (期权反推):**")
            msg_lines.append(f"   IV: {a['iv']:.1%} | 预期波幅: ±{a['move']:.2f}")
            msg_lines.append(f"   📉 `{a['low']:,.2f}`  ~  📈 `{a['high']:,.2f}`")
        
        # 方案 B 输出
        b = data['method_b']
        if b['valid']:
            msg_lines.append(f"🔸 **方案B (恐慌指数):**")
            msg_lines.append(f"   IV: {b['iv']:.1%} | 预期波幅: ±{b['move']:.2f}")
            msg_lines.append(f"   📉 `{b['low']:,.2f}`  ~  📈 `{b['high']:,.2f}`")
        else:
            if asset['vol_index']: # 如果配置了指数但没获取到
                msg_lines.append(f"🔸 方案B: 暂无数据")

    # 发送
    final_msg = "\n".join(msg_lines)
    send_telegram(final_msg)
    print("推送完成！")

if __name__ == "__main__":
    main()