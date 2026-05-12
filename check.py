with open(r'C:\Users\user\Downloads\AI-Congress-Backend\backend\ai_caller.py', 'rb') as f:
    raw = f.read()
# BOM 제거 및 깨진 바이트 정리
raw = raw.replace(b'\xef\xbb\xbf', b'')  # UTF-8 BOM
text = raw.decode('utf-8', errors='replace')
# 깨진 문자 확인
for i, line in enumerate(text.split('\n'), 1):
    if '\ufffd' in line:
        print(f'Line {i}: {repr(line)}')
print('검사 완료')
