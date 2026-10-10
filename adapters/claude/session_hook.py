import json
import sys


def context(event):
    identifier=event.get('session_id')
    if not isinstance(identifier,str) or not identifier or len(identifier)>200:return None
    return {'hookSpecificOutput':{'hookEventName':'SessionStart','additionalContext':
        '回顾伴随：当前 session_id='+json.dumps(identifier,ensure_ascii=False)+
        '。用户开始/继续回顾时，先 connect_session 绑定实际仓库路径和此ID，再 read_review 恢复。恢复不解释、不发送问题、不推进文件。总览请求交付总览后结束；请解释/下一份默认只讲并保存当前一份后结束，不自动批量标记。'}}

if __name__=='__main__':
    try:
        result=context(json.loads(sys.stdin.read(32000)))
        if result:print(json.dumps(result,ensure_ascii=False))
    except (ValueError,TypeError,AttributeError):pass
