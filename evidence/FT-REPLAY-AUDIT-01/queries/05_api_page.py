# -*- coding: utf-8 -*-
"""05 系统 API / 页面 / 时区单位 / 交易ID / 研究状态核对。"""
from __future__ import annotations
import json, os, re, subprocess, sys
import pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from _common import E09, OUT, R01, ROOT, jdump, load_replay, load_source, write_csv

CHROME="/Users/boos/Desktop/Google Chrome.app/Contents/MacOS/Google Chrome"
BASE="http://127.0.0.1:8787/api/v1/backtests"

def curl_json(url, timeout=30):
    p=subprocess.run(["curl","-s","-m",str(timeout),url],capture_output=True,text=True)
    try: return json.loads(p.stdout)
    except Exception as e: return {"__error__":str(e),"raw":p.stdout[:200]}

def main():
    rows=[]
    # API batch
    runs=curl_json(BASE+"/batches/runs_ft_entry_09_research/runs")
    summ=curl_json(BASE+"/batches/runs_ft_entry_09_research/summary")
    items=runs.get("items",[]) if isinstance(runs,dict) else []
    std=[x for x in items if str(x.get("run_id","")).startswith("run_WX_EVENT_TREND_V0_1_FT09_")]
    sym=[x for x in items if str(x.get("run_id","")).startswith("run_FT_ENTRY_09_")]
    b=summ.get("batch",{}) if isinstance(summ,dict) else {}
    api={"runs_total":len(items),"std":len(std),"symbol":len(sym),
         "std_trades":int(sum(x.get("trades_count") or 0 for x in std)),
         "symbol_trades":int(sum(x.get("trades_count") or 0 for x in sym)),
         "batch_trades":b.get("trades_total"),"batch_net":b.get("net_pnl_sum"),
         "expected_runs":27,"expected_trades":4325,"expected_net":-3678.74,
         "error":runs.get("__error__") if isinstance(runs,dict) else None}
    first_rid=items[0].get("run_id") if items else None
    tr=curl_json(BASE+f"/{first_rid}/trades") if first_rid else {}
    tr_items=tr.get("items",[]) if isinstance(tr,dict) else []
    tr_missing=(tr.get("missing_reason") if isinstance(tr,dict) else None)
    api["first_run"]=first_rid; api["first_run_trades"]=len(tr_items); api["first_run_missing"]=tr_missing
    rows.append({"item":"run_id → trades 可解析","expected":"列表中的 run_id 能打开交易明细",
                 "actual":f"{first_rid}: {len(tr_items)} 条; {tr_missing}",
                 "pass": bool(tr_items)})
    rows.append({"item":"API 批次分组","expected":"27 runs / 4325 trades / net -3678.74",
                 "actual":f"{api['runs_total']} runs / {api['batch_trades']} trades / net {api['batch_net']}",
                 "pass": bool(api["runs_total"]==27 and api["batch_trades"]==4325)})
    # page access
    main_code=subprocess.run(["curl","-s","-o","/dev/null","-w","%{http_code}","-m","10","http://127.0.0.1:3000/"],capture_output=True,text=True).stdout
    static_code=subprocess.run(["curl","-s","-o","/dev/null","-w","%{http_code}","-m","10","http://127.0.0.1:8089/"],capture_output=True,text=True).stdout
    rows.append({"item":"系统原生页面(3000)","expected":"HTTP 200 可打开","actual":f"HTTP {main_code}","pass":main_code=="200"})
    rows.append({"item":"专属复盘页(8089)","expected":"HTTP 200 且能显示记录","actual":f"HTTP {static_code}","pass":False})
    # headless page
    dom_p=os.path.join(OUT,"05_page_dom.html"); shot_p=os.path.join(OUT,"05_page_screenshot.png")
    console_p=os.path.join(ROOT,"evidence/FT-REPLAY-AUDIT-01/logs/05_chrome_console.log")
    if os.path.isfile(CHROME) and static_code=="200":
        subprocess.run([CHROME,"--headless=new","--disable-gpu","--no-sandbox",
                        "--user-data-dir=/tmp/chrome-replay-audit","--window-size=1600,1000",
                        "--virtual-time-budget=15000","--enable-logging=stderr",
                        f"--screenshot={shot_p}","--dump-dom","http://127.0.0.1:8089/"],
                       capture_output=True,text=True,timeout=120)
        # dump-dom and screenshot in one run may conflict; run separately
        r=subprocess.run([CHROME,"--headless=new","--disable-gpu","--no-sandbox",
                          "--user-data-dir=/tmp/chrome-replay-audit2","--virtual-time-budget=15000",
                          "--enable-logging=stderr","--dump-dom","http://127.0.0.1:8089/"],
                         capture_output=True,text=True,timeout=120)
        open(dom_p,"w",encoding="utf-8").write(r.stdout)
        open(console_p,"w",encoding="utf-8").write(r.stderr)
        m=re.search(r"显示\s*(\d+)\s*/\s*(\d+)\s*项",r.stdout)
        display=f"{m.group(1)}/{m.group(2)}" if m else "NOT_FOUND"
        chart_err="addCandlestickSeries is not a function" in r.stderr
        rows.append({"item":"页面实际渲染","expected":"显示 740/9128（C1+FILLED）","actual":f"显示 {display}; chart_err={chart_err}","pass":display!="0/0" and not chart_err})
    else:
        rows.append({"item":"页面实际渲染","expected":"可打开","actual":"无法访问/浏览器不可用","pass":False})
    # time/unit
    rep=load_replay()
    def parse_utc(s):
        return pd.to_datetime(s.astype(str).str.replace(" UTC","",regex=False),utc=True,errors="coerce")
    def parse_local(s):
        return pd.to_datetime(s.astype(str).str.replace(" CST","",regex=False),utc=True,errors="coerce")
    utc_mismatch=int((parse_utc(rep["orig_signal_time_utc"])!=pd.to_datetime(rep["orig_signal_time_ms"],unit="ms",utc=True)).sum())
    local_mismatch=int((parse_local(rep["orig_signal_time_local"])!=(pd.to_datetime(rep["orig_signal_time_ms"],unit="ms",utc=True)+pd.Timedelta(hours=8))).sum())
    rows.append({"item":"时间单位/时区","expected":"ms=UTC；local=UTC+8；0 mismatch",
                 "actual":f"orig_utc_mismatch={utc_mismatch}; orig_local_mismatch={local_mismatch}","pass":utc_mismatch==0 and local_mismatch==0})
    # trade id mapping
    src=load_source(); f=rep[rep.execution_status=="FILLED"]
    m=f.merge(src[["trade_key"]],on="trade_key",how="left")
    rows.append({"item":"交易ID映射","expected":"可追溯到源账本",
                 "actual":f"replay trade_id 合成；源账本无 trade_id；key 匹配 {m.trade_key.notna().sum()}/{len(m)}",
                 "pass": int(m.trade_key.notna().sum())==len(m)})
    # research status/version
    meta=json.load(open(os.path.join(ROOT,"回测日志/event_trend_v0_1/runs_ft_entry_09_research/batch_meta.json"),encoding="utf-8"))
    versions=set(rep["strategy_version"].dropna().astype(str)) | set(rep["data_version"].dropna().astype(str))
    rows.append({"item":"研究状态/版本","expected":"research_period_result; formal_pass_allowed=false; FT_ENTRY_09/FT-FACTOR-03",
                 "actual":f"{meta.get('category')}; {meta.get('formal_pass_allowed')}; {sorted(versions)}",
                 "pass": meta.get("category")=="research_period_result" and meta.get("formal_pass_allowed") is False})
    df=pd.DataFrame(rows); write_csv(df,"05_API页面核对_v1.csv")
    info={"api":api,"page_main_http":main_code,"page_static_http":static_code,
          "page_display":next((x["actual"] for x in rows if x["item"]=="页面实际渲染"),None),
          "time_utc_mismatch":utc_mismatch,"time_local_mismatch":local_mismatch,
          "trade_id_native":False,"trade_id_key_match":int(m.trade_key.notna().sum()),
          "batch_meta_runs_total":meta.get("runs_total"),
          "note":"页面必须实际打开；当前专属页有 chart 运行时错误，原生页 500/批次混组，均未通过。"}
    jdump(os.path.join(OUT,"05_API页面摘要_v1.json"),info)
    print(df.to_string(index=False)); print(json.dumps(info,ensure_ascii=False,indent=1,default=str))
if __name__=="__main__": main()
