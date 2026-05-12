with open(r'C:\Users\user\Downloads\AI-Congress-Backend\backend\ai_caller.py', 'rb') as f:
    content = f.read()
content = content.decode('utf-8', errors='ignore')
with open(r'C:\Users\user\Downloads\AI-Congress-Backend\backend\ai_caller.py', 'w', encoding='utf-8', newline='\n') as f:
    f.write(content)
print('완료')
