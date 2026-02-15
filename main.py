name: Gold Volatility Bot

# === 关键：赋予写入权限，否则无法保存 CSV ===
permissions:
  contents: write

on:
  schedule:
    # GitHub 使用 UTC 时间 (北京时间 - 8小时)
    
    # 1. 早盘：北京时间 08:30 (UTC 00:30)
    # 此时会创建一行新记录
    - cron: '30 0 * * 1-5'
    
    # 2. 晚盘：美股开盘后 (北京时间 22:45 或 23:45)
    # 这里设为 UTC 14:45，确保无论是冬令时还是夏令时，美股都已经开盘
    # 此时会覆盖更新早上的记录
    - cron: '45 14 * * 1-5'

  # 允许手动触发
  workflow_dispatch:

jobs:
  run-bot:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.9'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run script
        env: 
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          CHAT_ID: ${{ secrets.CHAT_ID }}
        run: python main.py

      # === 关键步骤：把生成的 CSV 提交回仓库 ===
      - name: Commit and Push CSV
        run: |
          git config --global user.name "GitHub Action Bot"
          git config --global user.email "actions@github.com"
          # 检查是否有 gold_record.csv 的变化
          git add gold_record.csv
          # 如果有变化则提交，没变化则忽略 (防止报错)
          git commit -m "Update Daily Record" || echo "No changes to commit"
          git push
