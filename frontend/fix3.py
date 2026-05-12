lines = open(r'C:\Users\user\Downloads\AI-Congress-Backend\backend\debate_engine.py', encoding='utf-8').read()
old = 'await asyncio.gather(*[_research_one(m) for m in self.members])'
new = 'for m in self.members:\n            await _research_one(m)'
lines = lines.replace(old, new)
open(r'C:\Users\user\Downloads\AI-Congress-Backend\backend\debate_engine.py', 'w', encoding='utf-8').write(lines)
print('완료')
