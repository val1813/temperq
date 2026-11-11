"""Offline audit web application; no training jobs, providers or account scheduler."""
from __future__ import annotations

import argparse
import html
from pathlib import Path
import secrets
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles


def create_app(workdir: Path, *, port: int = 8765):
    from . import strategy_audit_ui as view, governance_review_ui, execution_evidence_ui, validation_evidence_ui

    workdir = Path(workdir).resolve()
    token = secrets.token_urlsafe(24)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.token = token
    app.state.edition = 'audit'
    app.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')
    allowed_hosts = {f'127.0.0.1:{port}', f'localhost:{port}', 'testserver'}

    def guard(req: Request, value):
        if req.headers.get('host') not in allowed_hosts:
            raise HTTPException(403, 'bad host')
        if not isinstance(value, str) or not secrets.compare_digest(value, token):
            raise HTTPException(403, 'bad token')

    def page(title, body, refresh=False, *, identity='', tab='governance'):
        navigation = ('<nav class="nav" aria-label="主导航"><a href="/strategy-audits/new" class="on" aria-current="page">策略治理</a></nav>'
                      + view.single_page_navigation(workdir))
        markup = ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
                  '<meta name="viewport" content="width=device-width,initial-scale=1">'
                  f'<title>{html.escape(title)} · TemperQ</title>'
                  '<link rel="icon" type="image/png" href="/static/temperq-favicon.png">'
                  '<link rel="stylesheet" href="/static/audit-shell.css">'
                  '<link rel="stylesheet" href="/static/governance-review.css">'
                  '<link rel="stylesheet" href="/static/strategy-audit.css">'
                  '<script src="/static/experience.js" defer></script></head><body>'
                  '<header class="top"><div class="top-in"><a class="brand" href="/"><img src="/static/temperq-logo.png" alt="" width="32" height="32">TemperQ</a>'
                  + navigation +
                  '</div></header><main>' + body + '</main>'
                  '<footer class="foot">本地审计 · 材料只保存在本机 · 结果以证据覆盖范围为准</footer>'
                  + view.help_panel() + '</body></html>')
        return HTMLResponse(markup)

    @app.get('/')
    def home():
        return RedirectResponse('/strategy-audits/new', 303)

    # 审计版只保留“上传 → 报告 / 对比报告”一条路径；旧工作区页面统一回到首页。
    def _home_redirect():
        return RedirectResponse('/strategy-audits/new', 303)
    for legacy in ('/strategy-governance', '/strategy-attribution', '/strategy-comparison',
                   '/strategy-simulation', '/strategy-audits', '/settings'):
        app.add_api_route(legacy, _home_redirect, methods=['GET'], include_in_schema=False)

    governance_review_ui.register(app, workdir, page=page, guard=guard, token=token)
    view.register(app, workdir, page=page, guard=guard, token=token, audit_only=True)
    execution_evidence_ui.register(app, workdir, page=page, guard=guard, token=token)
    validation_evidence_ui.register(app, workdir, page=page, guard=guard, token=token)
    return app


def serve(workdir: Path, *, port=8765, open_browser=True):
    import uvicorn
    if not 1 <= port <= 65535:
        raise ValueError('端口须在 1–65535 之间')
    app = create_app(workdir, port=port)
    if open_browser:
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f'http://127.0.0.1:{port}/')).start()
    print(f'TemperQ 审计版：http://127.0.0.1:{port}/', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')


def main(argv=None):
    parser = argparse.ArgumentParser(description='TemperQ 离线策略审计')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    serve(Path.cwd(), port=args.port, open_browser=not args.no_browser)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
