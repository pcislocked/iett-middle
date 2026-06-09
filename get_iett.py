with open(r"C:\Users\amdin\Desktop\iett-project\iett-middle\app\services\iett_client.py", "r", encoding="utf-8") as f:
    content = f.read()
import re
match = re.search(r"def get_announcements.*?(?=def |\Z)", content, re.DOTALL)
if match:
    print(match.group(0))
else:
    print("Not found")
