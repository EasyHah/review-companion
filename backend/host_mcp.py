#!/usr/bin/env python3
"""Shared Claude/Codex STDIO adapter; the host owns inference and credentials."""
import argparse
import json
import os
from pathlib import Path
import secrets
import threading
import webbrowser

from browser_server import Bridge, create_server, repository_root
from review_companion import serve, obj, ID, validate
from companion_store import StoreError
from git_snapshot import SnapshotError, _git_root
from runtime import load_config, apply_tools, data_directory

VERSION='0.4.0'
SESSION_SCHEMA=obj({'session_id':ID,'repository_path':ID})


class HostService:
    def __init__(self,host='claude',repository=None,config=None,data_dir=None):
        if host not in ('claude','codex'):raise ValueError('unsupported host')
        self.host=host
        self.config=load_config() if config is None else config
        apply_tools(self.config)
        self.exact_data=data_dir
        self.repository=repository_root(repository or os.getcwd())
        self.bridge=Bridge(data_directory(self.repository,data_dir,self.config),self.repository,host)
        self.bridge.switch(host+' MCP')
        self.session_id=None
        self.server=None
        self.thread=None

    def bind(self,session_id=None,repository_path=None):
        if repository_path:
            repository=Path(_git_root(repository_path)).resolve()
            if repository != self.repository:
                self.close()
                self.repository=repository
                self.bridge=Bridge(data_directory(repository,self.exact_data,self.config),repository,self.host)
                self.session_id=None
        identifier=session_id or secrets.token_urlsafe(16)
        if len(identifier)>200 or any(c in identifier for c in '\x00\r\n'):
            raise StoreError('invalid_argument','会话标识无效')
        if identifier != self.session_id:
            self.session_id=identifier
            self.bridge.switch(identifier)
        return {'session_bound':True,**self.bridge.session_info(),'data_directory':str(self.bridge.service.store.directory)}

    def open(self,arguments):
        result=self.bridge.dispatch('open_review',arguments)
        if result.get('isError'):return result
        if self.server is None:
            self.server=create_server(self.bridge)
            self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
            self.thread.start()
        url='http://'+self.server.address+'/#token='+self.bridge.browser_token
        data=result['structuredContent']
        data.update(browser_url=url,input_mode='copy',host=self.host,session=self.bridge.session,
                    message='在浏览器打开此链接；面板复制问题，由你粘贴到当前聊天发送。')
        result['content']=[{'type':'text','text':json.dumps(data,ensure_ascii=False)}]
        if os.environ.get('REVIEW_COMPANION_OPEN_BROWSER')=='1':webbrowser.open(url)
        return result

    def rpc(self,method,params):
        if method=='initialize':
            return {'protocolVersion':params.get('protocolVersion','2025-03-26'),'capabilities':{'tools':{}},
                    'serverInfo':{'name':'review-companion','version':VERSION},
                    'instructions':'Use the current host model. First connect_session with the actual repository_path in each new chat; use host session_id if available, otherwise omit it for a new binding. Restore with read_review, never auto-explain. open_review returns a private loopback browser link. Browser buttons copy prompts for manual sending. Read fixed evidence with read_fixed_evidence. Save explanation and impact conclusions independently. Overview requests end after the feature overview and panel link. Risk analysis and behavioral diagrams are separate, on-demand conversation skills. set_file_risks only highlights file notes; never marks files reviewed. Explain/next requests cover one selected file, then save and stop unless the user explicitly requests a batch. Reading related files does not authorize marking them reviewed.'}
        if method=='ping':return {}
        if method=='tools/list':
            tools=[{k:v for k,v in item.items() if k!='_meta'} for item in self.bridge.service.tools() if item['name'] not in ('open_editor','open_project')]
            tools.append({'name':'connect_session','description':'在新聊天先绑定实际 Git 仓库路径；可传宿主提供的 session_id，否则生成新绑定。只连接/恢复，不解释或发送问题。','inputSchema':SESSION_SCHEMA,'annotations':{'readOnlyHint':False,'destructiveHint':False,'openWorldHint':False}})
            return {'tools':tools}
        if method=='tools/call':
            name,args=params.get('name'),params.get('arguments',{})
            try:
                if name=='connect_session':
                    validate(args,SESSION_SCHEMA)
                    data=self.bind(**args)
                    return {'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}],'structuredContent':data,'isError':False}
                if name in ('open_editor','open_project'):raise StoreError('forbidden','请由用户点击浏览器面板的项目、源码或 Diff 按钮')
                return self.open(args) if name=='open_review' else self.bridge.dispatch(name,args)
            except (StoreError,SnapshotError) as error:
                data={'error':{'code':error.code,'message':str(error)}}
                return {'content':[{'type':'text','text':json.dumps(data,ensure_ascii=False)}],'structuredContent':data,'isError':True}
        raise StoreError('method_not_found','不支持此 MCP 方法')

    def close(self):
        if self.server:
            self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
            self.server=None;self.thread=None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',choices=['claude','codex'],default='claude')
    parser.add_argument('--repository');parser.add_argument('--data-dir')
    args=parser.parse_args()
    service=HostService(args.host,args.repository,data_dir=args.data_dir)
    try:serve(service)
    finally:service.close()

if __name__=='__main__':main()
