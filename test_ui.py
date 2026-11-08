"""Headless browser smoke test against an isolated local app (Edge on Windows)."""
import os
import json
import tempfile
import threading
from pathlib import Path
os.environ.update(DATA_DIR=tempfile.mkdtemp(),SESSION_SECRET='ui-test-only',
                  ADMIN_PASSWORD='ui-test-password',DISABLE_WORKER='1')
import app
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright


def test():
    server=make_server('127.0.0.1',0,app.app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    errors=[]
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport=dict(width=1440,height=1000))
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://127.0.0.1:'+str(server.server_port))
        page.locator('#password').fill('ui-test-password')
        page.locator('#loginForm button').click()
        page.locator('#app').wait_for(state='visible')
        import time
        app.db("INSERT INTO results(provider_id,provider_name,created,status,download,upload,ping) VALUES (1,'Ookla',?,'ok',400,200,12)",(time.time()-200,))
        app.db("INSERT INTO results(provider_id,provider_name,created,status,error) VALUES (1,'UI failure marker',?,'error','UI failure')",(time.time()-100,))
        page.locator('#filter').click()
        page.locator('#history').get_by_text('UI failure marker',exact=True).wait_for()
        with page.expect_response(lambda r:'/api/results?' in r.url and 'hide_failed=1' in r.url):page.locator('#hideFailed').check()
        page.wait_for_function("document.getElementById('total').textContent==='1'")
        assert 'UI failure marker' not in page.locator('#history').inner_text()
        page.reload();page.locator('#app').wait_for(state='visible')
        assert page.locator('#hideFailed').is_checked()
        page.locator('#exportPdf').click()
        page.locator('#reportDialog').wait_for(state='visible')
        assert page.locator('#reportHideFailed').is_checked()
        for checkbox in page.locator('[name=reportSection]').all():checkbox.uncheck()
        page.locator('#generateReport').click()
        assert 'pelo menos uma' in page.locator('#reportError').inner_text()
        page.locator('[name=reportSection][value=uptime]').check()
        with page.expect_download() as pending:page.locator('#generateReport').click()
        download=pending.value
        Path('qa').mkdir(exist_ok=True)
        download.save_as('qa/uptime-only-ui.pdf')
        from pypdf import PdfReader
        text='\n'.join(p.extract_text() for p in PdfReader('qa/uptime-only-ui.pdf').pages)
        assert 'Disponibilidade e uptime' in text and 'Histórico completo de velocidade' not in text
        page.locator('#reportDialog').wait_for(state='hidden')
        page.get_by_role('button',name='Uptime',exact=True).click()
        page.get_by_role('button',name='+ Adicionar monitor',exact=True).click()
        page.locator('#mName').fill('Servidor local')
        page.locator('#mTarget').fill('http://127.0.0.1:8095/health')
        page.locator('#mEnabled').uncheck()
        page.locator('#saveMonitor').click()
        page.locator('.monitor-card').wait_for()
        assert page.locator('.monitor-card').count()==1
        assert 'Pausado' in page.locator('.monitor-card').inner_text()
        # Seed observations through the same storage path for a useful graph and incident view.
        import time
        p=app.uptime_service['monitors']()[0]
        p['enabled']=True
        p['slow_ms']=25
        app.db('UPDATE monitors SET value=? WHERE id=?',(json.dumps(p),p['id']))
        app.db('DELETE FROM uptime_checks WHERE monitor_id=?',(p['id'],))
        t=time.time()-600
        for i in range(10):
            ok=i not in (3,4,5)
            app.uptime_service['record'](p,dict(ok=ok,ms=20+i if ok else None,error='' if ok else 'timeout',code=200 if ok else None),t+i*60)
        page.locator('#updateUptime').click()
        page.locator('#incidentHistory tr').filter(has_text='Recuperado').wait_for()
        page.get_by_text('Resposta lenta',exact=True).wait_for()
        Path('qa').mkdir(exist_ok=True)
        page.screenshot(path='qa/uptime-desktop.png',full_page=True)
        page.locator('[data-m-maint]').click()
        page.locator('.monitor-card .badge').get_by_text('Manutenção',exact=True).wait_for()
        page.locator('[data-m-maint]').click()
        page.locator('.monitor-card .badge').get_by_text('Sem dados',exact=True).wait_for()
        app.db("INSERT INTO results(provider_id,provider_name,created,status,download,upload,ping,jitter,loss) VALUES (1,'Ookla',?,'ok',400,200,12,2,0)",(time.time()-100,))
        page.get_by_role('button',name='Análises',exact=True).click()
        page.locator('#contractDown').fill('500')
        page.locator('#contractUp').fill('250')
        page.locator('#targetPct').fill('80')
        page.locator('#targetForm button').click()
        page.locator('#targetCards').get_by_text('500 Mbps contratados',exact=True).wait_for()
        assert page.locator('#sharedFilters').is_visible()
        assert page.locator('#stability tr').count()==5
        page.locator('[data-heat]').first.click()
        assert '400 Mbps' in page.locator('#heatDetail').inner_text()
        page.screenshot(path='qa/analysis-desktop.png',full_page=True)
        page.locator('#exportPdf').click()
        for checkbox in page.locator('[name=reportSection]').all():checkbox.check()
        page.screenshot(path='qa/report-dialog-desktop.png',full_page=True)
        with page.expect_download() as pending:page.locator('#generateReport').click()
        pending.value.save_as('qa/complete-ui.pdf')
        page.locator('#reportDialog').wait_for(state='hidden')
        page.locator('#exportExcel').click()
        with page.expect_download() as pending:page.locator('#generateReport').click()
        pending.value.save_as('qa/complete-ui.xlsx')
        page.locator('#reportDialog').wait_for(state='hidden')
        page.set_viewport_size(dict(width=390,height=844))
        page.screenshot(path='qa/analysis-mobile.png',full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'Horizontal page overflow'
        page.locator('#exportPdf').click()
        page.screenshot(path='qa/report-dialog-mobile.png',full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        page.locator('#closeReport').click()
        page.get_by_role('button',name='Uptime',exact=True).click()
        page.screenshot(path='qa/uptime-mobile.png',full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), 'Horizontal page overflow'
        page.locator('[data-m-edit]').click()
        assert page.locator('#mName').input_value()=='Servidor local'
        page.locator('#mKind').select_option('tcp')
        assert page.locator('#mPortField').is_visible() and not page.locator('#mExpectedField').is_visible()
        page.locator('#closeMonitor').click()
        page.on('dialog',lambda d:d.accept())
        page.locator('[data-m-delete]').click()
        page.get_by_text('Arquivado',exact=True).wait_for()
        assert not errors,errors
        browser.close()
    server.shutdown()
    print('UI: login, navigation, create/edit/archive, maintenance fields, goals, history, desktop/mobile and JS errors: OK')

if __name__=='__main__':test()
