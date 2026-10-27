// FT-REPLAY-01: 交互式系统复盘工作台前端逻辑
let allRecords = [];
let filteredRecords = [];
let currentRecord = null;
let useUtc = true;

let chart = null;
let candleSeries = null;
let p0PriceLine = null;
let cachedCandles = {};

// 初始化
window.addEventListener("DOMContentLoaded", async () => {
  initChart();
  await loadData();
});

function initChart() {
  const container = document.getElementById("tv_chart");
  chart = LightweightCharts.createChart(container, {
    layout: {
      background: { color: "#0b1120" },
      textColor: "#94a3b8",
    },
    grid: {
      vertLines: { color: "#1e293b" },
      horzLines: { color: "#1e293b" },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
    },
    timeScale: {
      borderColor: "#334155",
      timeVisible: true,
      secondsVisible: false,
    },
    rightPriceScale: {
      borderColor: "#334155",
    },
  });

  candleSeries = chart.addCandlestickSeries({
    upColor: "#4ade80",
    downColor: "#f87171",
    borderVisible: false,
    wickUpColor: "#4ade80",
    wickDownColor: "#f87171",
  });

  window.addEventListener("resize", () => {
    if (chart && container) {
      chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
    }
  });
}

async function loadData() {
  try {
    const res = await fetch("./data/replay_data.json");
    const json = await res.json();
    allRecords = json.records || [];
    applyFilters();
  } catch (e) {
    console.error("加载复盘数据包失败:", e);
  }
}

function toggleTimezone() {
  useUtc = !useUtc;
  document.getElementById("btn_tz").innerText = useUtc ? "当前时区: UTC" : "当前时区: 本地 (CST)";
  renderList();
  if (currentRecord) {
    showDetail(currentRecord);
  }
}

function formatTime(ms) {
  if (!ms || ms <= 0) return "未知";
  const d = new Date(ms);
  if (useUtc) {
    return d.toISOString().replace("T", " ").replace(".000Z", " UTC");
  } else {
    return d.toLocaleString("zh-CN", { hour12: false }) + " CST";
  }
}

function applyFilters() {
  const mode = document.getElementById("sel_mode").value;
  const param = document.getElementById("sel_param").value;
  const status = document.getElementById("sel_status").value;
  const symQuery = document.getElementById("inp_sym").value.trim().toUpperCase();

  filteredRecords = allRecords.filter(r => {
    if (mode !== "ALL" && r.timing_mode !== mode) return false;
    if (param !== "ALL" && r.param_suffix !== param) return false;
    if (status !== "ALL" && r.execution_status !== status) return false;
    if (symQuery && !r.clean_symbol.includes(symQuery) && !r.pair.includes(symQuery)) return false;
    return true;
  });

  document.getElementById("txt_filter_count").innerText = `显示 ${filteredRecords.length} / ${allRecords.length} 项`;
  
  const pnlSum = filteredRecords.reduce((acc, r) => acc + (r.net_pnl_usdt || 0), 0);
  const pnlEl = document.getElementById("txt_pnl_sum");
  pnlEl.innerText = `净盈亏合计: ${pnlSum.toFixed(2)} USDT`;
  pnlEl.className = pnlSum >= 0 ? "pnl-pos" : "pnl-neg";

  renderList();

  // 默认选中第一项
  if (filteredRecords.length > 0) {
    selectRecord(filteredRecords[0]);
  }
}

function renderList() {
  const container = document.getElementById("record_list");
  container.innerHTML = "";

  // 渲染前 300 条避免长列表卡顿
  const slice = filteredRecords.slice(0, 300);
  slice.forEach(r => {
    const card = document.createElement("div");
    card.className = "record-card" + (currentRecord === r ? " active" : "");
    card.onclick = () => selectRecord(r);

    let statusBadge = "";
    if (r.execution_status === "FILLED") {
      statusBadge = `<span class="badge badge-pass">成交</span>`;
    } else if (r.execution_status === "REJECTED") {
      statusBadge = `<span class="badge badge-err">拒单</span>`;
    } else {
      statusBadge = `<span class="badge badge-warn">未通过</span>`;
    }

    const pnlClass = (r.net_pnl_usdt || 0) > 0 ? "pnl-pos" : ((r.net_pnl_usdt || 0) < 0 ? "pnl-neg" : "");
    const pnlText = r.execution_status === "FILLED" ? `${r.net_pnl_usdt > 0 ? "+" : ""}${r.net_pnl_usdt} U (${r.profit_ratio_pct}%)` : r.decision_reason;

    card.innerHTML = `
      <div class="card-top">
        <span class="card-sym">${r.clean_symbol}</span>
        <div class="card-meta">
          <span class="badge badge-research">${r.timing_mode} · ${r.param_suffix}</span>
          ${statusBadge}
        </div>
      </div>
      <div class="card-times">信号: ${formatTime(r.orig_signal_time_ms)}</div>
      <div class="card-pnl">
        <span style="color: var(--text-muted);">${r.trade_id}</span>
        <span class="${pnlClass}">${pnlText}</span>
      </div>
    `;
    container.appendChild(card);
  });
}

async function selectRecord(record) {
  currentRecord = record;
  
  // 更新高亮样式
  const cards = document.querySelectorAll(".record-card");
  cards.forEach(c => c.classList.remove("active"));
  
  showDetail(record);
  await loadAndRenderChart(record);
}

function showDetail(r) {
  document.getElementById("cur_title").innerText = `${r.clean_symbol} (${r.pair}) · 模式 ${r.timing_mode} · 参数 ${r.param_suffix}`;
  document.getElementById("cur_sub").innerText = `候选ID: ${r.candidate_id} | 交易ID: ${r.trade_id} | 状态: ${r.execution_status}`;

  // 顶部 Badge
  const bContainer = document.getElementById("cur_badges");
  bContainer.innerHTML = `
    <span class="badge badge-research">${r.timing_mode}</span>
    <span class="badge badge-warn">Vs:${r.vs} Z:${r.z}</span>
    <span class="badge ${r.execution_status === "FILLED" ? "badge-pass" : "badge-err"}">${r.execution_status}</span>
  `;

  // 审计时点面板
  document.getElementById("aud_sig_t").innerText = `${formatTime(r.orig_signal_time_ms)} (${r.orig_signal_time_ms})`;
  document.getElementById("aud_conf_t").innerText = r.confirm_close_time_ms ? `${formatTime(r.confirm_close_time_ms)} (${r.confirm_close_time_ms})` : "未知 (C0模式无此环节)";
  document.getElementById("aud_order_t").innerText = r.order_time_ms ? `${formatTime(r.order_time_ms)} (${r.order_time_ms})` : "未知 (未下单)";
  document.getElementById("aud_fill_t").innerText = r.fill_time_ms ? `${formatTime(r.fill_time_ms)} (${r.fill_time_ms})` : "未知 (未成交)";
  document.getElementById("aud_exit_t").innerText = r.exit_time_ms ? `${formatTime(r.exit_time_ms)} (${r.exit_time_ms})` : "未知 (未成交)";
  document.getElementById("aud_trade_id").innerText = r.trade_id;

  // 价格与仓位
  document.getElementById("aud_p0").innerText = r.initial_stop_P0 !== null ? r.initial_stop_P0 : "未知";
  document.getElementById("aud_entry_p").innerText = r.entry_price !== null ? r.entry_price : "未知";
  document.getElementById("aud_r").innerText = r.planned_risk_R !== null ? r.planned_risk_R : "未知";
  document.getElementById("aud_qty").innerText = r.trade_amount || "0";
  document.getElementById("aud_stake").innerText = r.stake_amount_usdt ? `${r.stake_amount_usdt} USDT` : "0 USDT";
  
  const pnlEl = document.getElementById("aud_net_pnl");
  pnlEl.innerText = r.execution_status === "FILLED" ? `${r.net_pnl_usdt} USDT (${r.profit_ratio_pct}%)` : "0 USDT";
  pnlEl.className = (r.net_pnl_usdt || 0) > 0 ? "audit-val pnl-pos" : ((r.net_pnl_usdt || 0) < 0 ? "audit-val pnl-neg" : "audit-val");

  document.getElementById("aud_exit_r").innerText = r.exit_reason;

  // 三模式对照卡片
  document.getElementById("c0_res").innerText = r.c0_result || "未成交";
  document.getElementById("c1_res").innerText = r.c1_result || "未成交";
  document.getElementById("c2_res").innerText = r.c2_result || "未成交";

  document.getElementById("c1_attr").innerText = r.cross_mode_category;
  document.getElementById("c2_attr").innerText = r.c2_result.includes("USDT") ? "成交" : "过滤/放弃";

  document.getElementById("c1_dec").innerText = r.timing_mode === "C1" ? r.decision_reason : "参见对照";
  document.getElementById("c2_dec").innerText = r.timing_mode === "C2" ? r.decision_reason : "参见对照";

  document.getElementById("c1_pnl").innerText = r.c1_result;
  document.getElementById("c2_pnl").innerText = r.c2_result;
}

async function loadAndRenderChart(r) {
  if (!chart || !candleSeries) return;

  const sym = r.clean_symbol;
  let candles = cachedCandles[sym];

  if (!candles) {
    try {
      const res = await fetch(`./candles/${sym}_15m.json`);
      if (res.ok) {
        candles = await res.json();
        cachedCandles[sym] = candles;
      }
    } catch (e) {
      console.warn(`未找到本地 K 线切片 ${sym}:`, e);
    }
  }

  // 若无对应切片，生成该事件周围的锚点切片
  if (!candles || candles.length === 0) {
    candles = generateSyntheticAnchorCandles(r);
  }

  // 转换成 Lightweight Charts 格式（秒戳）
  const chartData = candles.map(c => ({
    time: Math.floor(c.t / 1000),
    open: c.o,
    high: c.h,
    low: c.l,
    close: c.c,
  }));

  candleSeries.setData(chartData);

  // 设置 P0 虚线
  if (p0PriceLine) {
    candleSeries.removePriceLine(p0PriceLine);
    p0PriceLine = null;
  }
  if (r.initial_stop_P0 && r.initial_stop_P0 > 0) {
    p0PriceLine = candleSeries.createPriceLine({
      price: r.initial_stop_P0,
      color: "#94a3b8",
      lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed,
      axisLabelVisible: true,
      title: "原始止损 P0",
    });
  }

  // 设置四重标注 Markers
  const markers = [];

  // 1. 🟡 信号时刻 (1h收盘)
  if (r.orig_signal_time_ms) {
    markers.push({
      time: Math.floor(r.orig_signal_time_ms / 1000),
      position: "belowBar",
      color: "#facc15",
      shape: "circle",
      text: "🟡信号(1h收盘)",
    });
  }

  // 2. 🔵 确认时刻 (次根1h收盘)
  if (r.confirm_close_time_ms) {
    const isPassed = r.execution_status === "FILLED";
    markers.push({
      time: Math.floor(r.confirm_close_time_ms / 1000),
      position: "aboveBar",
      color: isPassed ? "#38bdf8" : "#f87171",
      shape: "square",
      text: isPassed ? "🔵确认通过" : `❌确认未通过: ${r.decision_reason}`,
    });
  }

  // 3. 🟢 实际入场 (15m成交)
  if (r.fill_time_ms && r.execution_status === "FILLED") {
    markers.push({
      time: Math.floor(r.fill_time_ms / 1000),
      position: "belowBar",
      color: "#4ade80",
      shape: "arrowUp",
      text: `🟢买入: ${r.entry_price}`,
    });
  }

  // 4. 🔴 实际出场 (平仓)
  if (r.exit_time_ms && r.execution_status === "FILLED") {
    markers.push({
      time: Math.floor(r.exit_time_ms / 1000),
      position: "aboveBar",
      color: "#f87171",
      shape: "arrowDown",
      text: `🔴平仓: ${r.exit_price} (${r.exit_reason})`,
    });
  }

  candleSeries.setMarkers(markers.sort((a, b) => a.time - b.time));

  // 自动聚焦到事件发生区间
  const focusTime = r.fill_time_ms || r.orig_signal_time_ms;
  if (focusTime) {
    const focusSec = Math.floor(focusTime / 1000);
    const rangeSec = 50 * 900; // 50 根 15m
    chart.timeScale().setVisibleRange({
      from: focusSec - rangeSec,
      to: focusSec + rangeSec * 2,
    });
  }
}

function generateSyntheticAnchorCandles(r) {
  // 当币种未预缓存时，以信号/成交价为基准生成微距插值蜡烛
  const baseP = r.entry_price || r.initial_stop_P0 || 100.0;
  const t0 = (r.orig_signal_time_ms || Date.now()) - 20 * 900 * 1000;
  const out = [];
  for (let i = 0; i < 60; i++) {
    const t = t0 + i * 900 * 1000;
    out.push({
      t: t,
      o: baseP * (1 + Math.sin(i / 5) * 0.005),
      h: baseP * (1 + Math.sin(i / 5) * 0.005 + 0.003),
      l: baseP * (1 + Math.sin(i / 5) * 0.005 - 0.003),
      c: baseP * (1 + Math.sin((i + 1) / 5) * 0.005),
      v: 1000
    });
  }
  return out;
}
