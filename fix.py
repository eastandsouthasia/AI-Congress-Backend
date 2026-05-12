content = open(r'C:\Users\user\Downloads\AI-Congress-Backend\backend\ai_caller.py', encoding='utf-8').read()
content = content.replace('parent.parent  # AI-Congress-Backend', 'parent  # backend')
open(r'C:\Users\user\Downloads\AI-Congress-Backend\backend\ai_caller.py', 'w', encoding='utf-8').write(content)
print('완료')
