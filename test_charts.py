"""Exercise actual canvas interactions with dense data and a real local Flask server."""
import json,math,os,sqlite3,tempfile,threading,time
from pathlib import Path
os.environ.update(DATA_DIR=tempfile.mkdtemp(),SESSION_SECRET='chart-test-only',ADMIN_PASSWORD='chart-test-password',DISABLE_WORKER='1')
import app
from uptime import validate
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright


def test():
    now=time.time();count=2000;start=now-(count-1)*90
    with sqlite3.connect(app.DATA+'/speedtest.db') as c:
        c.executemany('INSERT INTO results(provider_id,provider_name,created,status,download,upload,ping,jitter,loss) VALUES (?,?,?,?,?,?,?,?,?)',[
            (pid,'Test '+name,start+i*90,'ok',(160 if i==999 else 80+10*math.sin(i/8))+pid*4,30+pid*2+math.sin(i/20),12+pid+math.sin(i/16),2+pid/2,.1*pid)
            for i in range(count) for pid,name in [(1,'A'),(2,'B')]])
    p=validate(dict(name='Router test',kind='ping',target='127.0.0.1',enabled=False))
    pid=app.db('INSERT INTO monitors(value) VALUES (?)',(json.dumps(p),))
    for offset,ms in [(0,20),(3600,25),(7*3600,21),(8*3600,32)]:
        t=now-9*3600+offset
        app.db("INSERT INTO uptime_checks(monitor_id,created,valid_until,state,ok,ms,error) VALUES (?,?,?,'up',1,?,'')",(pid,t,t+95,ms))
    server=make_server('127.0.0.1',0,app.app,threaded=True);threading.Thread(target=server.serve_forever,daemon=True).start()
    errors=[];directory=Path('qa/charts');directory.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(channel='msedge',headless=True)
        context=browser.new_context(viewport=dict(width=1440,height=1100));page=context.new_page()
        page.on('pageerror',lambda e:errors.append(str(e)))
        url='http://127.0.0.1:'+str(server.server_port)
        page.goto(url);page.locator('#password').fill('chart-test-password');page.locator('#loginForm button').click()
        page.wait_for_function("Number(document.getElementById('downloadChart').dataset.visiblePoints)===4000")
        canvas=page.locator('#downloadChart');panel=canvas.locator('xpath=ancestor::div[contains(@class,"panel")][1]')
        full=page.evaluate("Number(downloadChart.dataset.rangeEnd)-Number(downloadChart.dataset.rangeStart)")
        panel.locator('[data-chart-action=in]').click()
        page.wait_for_function('full=>Number(downloadChart.dataset.rangeEnd)-Number(downloadChart.dataset.rangeStart)<full*.6',arg=full)
        page.wait_for_function('downloadChart.dataset.rangeStart===uploadChart.dataset.rangeStart')
        zoomed=page.evaluate('[downloadChart.dataset.rangeStart,downloadChart.dataset.rangeEnd]')
        panel.locator('[data-chart-action=back]').click()
        page.wait_for_function('start=>Number(downloadChart.dataset.rangeStart)<Number(start)',arg=zoomed[0])
        panel.locator('[data-chart-action=next]').click()
        page.wait_for_function('start=>Math.abs(Number(downloadChart.dataset.rangeStart)-Number(start))<1',arg=zoomed[0])
        page.evaluate('loadResults()')
        assert page.evaluate('[downloadChart.dataset.rangeStart,downloadChart.dataset.rangeEnd]')==zoomed
        panel.locator('[data-chart-action=reset]').click()
        page.wait_for_function('full=>Math.abs(Number(downloadChart.dataset.rangeEnd)-Number(downloadChart.dataset.rangeStart)-full)<1',arg=full)
        toggle=panel.locator('.chart-key button').filter(has_text='Test A');toggle.click()
        page.wait_for_function("Number(downloadChart.dataset.visiblePoints)===2000")
        assert toggle.get_attribute('aria-pressed')=='false';toggle.click()
        page.wait_for_function("Number(downloadChart.dataset.visiblePoints)===4000")
        canvas.focus();page.keyboard.press('Home')
        assert 'Test A' in panel.locator('.chart-tip').inner_text() and 'fixado' in panel.locator('.chart-tip').inner_text()
        assert 'Test A' in page.locator('#downloadChartSummary').inner_text()
        page.keyboard.press('Escape')
        box=canvas.bounding_box();page.mouse.move(box['x']+box['width']*.25,box['y']+120);page.mouse.down();page.mouse.move(box['x']+box['width']*.75,box['y']+120,steps=8);page.mouse.up()
        page.wait_for_function('full=>Number(downloadChart.dataset.rangeEnd)-Number(downloadChart.dataset.rangeStart)<full*.7',arg=full)
        panel.locator('select').select_option('3600')
        page.wait_for_function('Number(downloadChart.dataset.rangeEnd)-Number(downloadChart.dataset.rangeStart)<=3600.1')
        panel.locator('[data-chart-action=reset]').click()
        page.screenshot(path=str(directory/'desktop.png'),full_page=True)
        panel.locator('[data-chart-action=expand]').click();page.locator('.chart-modal').wait_for(state='visible')
        assert canvas.bounding_box()['width']>1000
        page.screenshot(path=str(directory/'expanded.png'))
        page.locator('.chart-modal-close').click();page.locator('.chart-modal').wait_for(state='detached')
        page.locator('#filterProvider').select_option('3');page.locator('#filter').click()
        page.wait_for_function("downloadChart.dataset.visiblePoints==='0'")
        assert panel.locator('[data-chart-action=in]').is_disabled()
        assert 'Aguardando' in page.locator('#downloadChartSummary').inner_text()
        page.locator('#filterProvider').select_option('');page.locator('#filter').click()
        page.wait_for_function("Number(downloadChart.dataset.visiblePoints)===4000")
        # Min/max sampling must retain short spikes instead of averaging them away.
        assert page.evaluate("chartSample(Array.from({length:10000},(_,i)=>({x:i,y:i===5011?9999:1})),500).some(p=>p.y===9999)")
        page.get_by_role('button',name='Uptime',exact=True).click()
        page.wait_for_function("Number(document.getElementById('uptimeChart').dataset.visiblePoints)===4")
        assert page.evaluate("chartViews.get('uptimeChart').series.length")>=2
        uptime=page.locator('#uptimeChart').locator('xpath=ancestor::div[contains(@class,"panel")][1]')
        assert uptime.locator('.chart-key button').count()==1
        page.screenshot(path=str(directory/'uptime.png'),full_page=True)
        mobile=browser.new_context(storage_state=context.storage_state(),has_touch=True,viewport=dict(width=390,height=844))
        m=mobile.new_page();m.on('pageerror',lambda e:errors.append(str(e)));m.goto(url)
        m.wait_for_function("Number(document.getElementById('downloadChart').dataset.visiblePoints)===4000")
        m.locator('#downloadChart').tap(position=dict(x=100,y=120))
        assert m.locator('#downloadChart').locator('..').locator('.chart-tip').is_visible()
        assert m.evaluate('document.documentElement.scrollWidth<=innerWidth')
        m.screenshot(path=str(directory/'mobile.png'),full_page=True)
        assert not errors,errors
        mobile.close();browser.close()
    server.shutdown()
    print('Charts: shared zoom, drag selection, pan controls, preserved viewport, interactive legends, keyboard/touch tooltips, expanded view, sampling spikes, uptime gaps and mobile layout OK')

if __name__=='__main__':test()
