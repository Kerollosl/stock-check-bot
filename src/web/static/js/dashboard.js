/* =========================================
   Stock Check Bot — Dashboard JS
   ========================================= */

const SCORE_COLORS = {
    high:   '#00ff88',
    mid:    '#ffcc00',
    low:    '#ff4466',
};

function scoreColor(s) {
    if (s >= 0.65) return SCORE_COLORS.high;
    if (s >= 0.45) return SCORE_COLORS.mid;
    return SCORE_COLORS.low;
}

function signalClass(signal) {
    return signal.toLowerCase().replace(/\s+/g, '-');
}

function formatLabel(key) {
    return key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

/* ---- TIMESTAMP ---- */
function updateTimestamp() {
    const el = document.getElementById('timestamp');
    if (el) el.textContent = new Date().toLocaleString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
    });
}
setInterval(updateTimestamp, 1000);
updateTimestamp();

/* ---- MACRO ---- */
async function loadMacro() {
    try {
        const res = await fetch('/api/macro');
        const data = await res.json();

        const grid = document.getElementById('macro-grid');
        grid.innerHTML = '';

        for (const [key, value] of Object.entries(data.scores)) {
            const color = scoreColor(value);
            const card = document.createElement('div');
            card.className = 'macro-card';
            card.innerHTML = `
                <div class="macro-label">${formatLabel(key)}</div>
                <div class="macro-score" style="color: ${color}">${value.toFixed(2)}</div>
                <div class="macro-bar">
                    <div class="macro-bar-fill" style="background: ${color};" data-width="${value * 100}"></div>
                </div>
            `;
            grid.appendChild(card);

            // Animate bar after append
            requestAnimationFrame(() => {
                const fill = card.querySelector('.macro-bar-fill');
                fill.style.width = fill.dataset.width + '%';
            });
        }

        // Overall
        const overall = document.getElementById('macro-overall-value');
        overall.textContent = data.overall.toFixed(2);
        overall.style.color = scoreColor(data.overall);

    } catch (e) {
        console.error('Macro load error:', e);
    }
}

/* ---- SPARKLINE CHART ---- */
function renderSparkline(containerId, data, color) {
    const el = document.getElementById(containerId);
    if (!el || !data || data.length === 0) return null;

    const options = {
        chart: {
            type: 'area',
            height: 90,
            sparkline: { enabled: true },
            animations: {
                enabled: true,
                easing: 'easeinout',
                speed: 1200,
            },
        },
        series: [{ data }],
        stroke: { curve: 'smooth', width: 2, colors: [color] },
        fill: {
            type: 'gradient',
            gradient: {
                shadeIntensity: 1,
                opacityFrom: 0.35,
                opacityTo: 0.0,
                stops: [0, 100],
                colorStops: [
                    { offset: 0, color, opacity: 0.3 },
                    { offset: 100, color, opacity: 0 },
                ],
            },
        },
        tooltip: {
            enabled: true,
            theme: 'dark',
            x: { show: false },
            y: { formatter: v => '$' + v.toFixed(2) },
        },
        colors: [color],
    };

    const chart = new ApexCharts(el, options);
    chart.render();
    return chart;
}

/* ---- GAUGE ANIMATION ---- */
function animateGauge(card, score, signal, confidence) {
    const circumference = 2 * Math.PI * 52; // r=52
    const offset = circumference * (1 - score);
    const color = scoreColor(score);

    const fill = card.querySelector('.gauge-fill');
    const scoreText = card.querySelector('.gauge-score');
    const signalText = card.querySelector('.gauge-signal');

    fill.style.stroke = color;
    fill.style.filter = `drop-shadow(0 0 8px ${color}40)`;

    // Animate offset
    requestAnimationFrame(() => {
        fill.style.strokeDashoffset = offset;
    });

    // Animate score number
    let current = 0;
    const duration = 1200;
    const startTime = performance.now();

    function animateNumber(now) {
        const elapsed = now - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
        current = score * eased;
        scoreText.textContent = current.toFixed(2);
        if (progress < 1) requestAnimationFrame(animateNumber);
    }
    requestAnimationFrame(animateNumber);

    signalText.textContent = signal;
    signalText.style.fill = color;
}

/* ---- STOCK CARD ---- */
function renderStockCard(ticker, data) {
    const card = document.getElementById(`card-${ticker}`);
    if (!card) return;

    card.classList.remove('skeleton');

    // Price badge
    const priceBadge = card.querySelector('.price-badge');
    const up = data.change_pct >= 0;
    priceBadge.textContent = `$${data.price.toFixed(2)}  ${up ? '+' : ''}${data.change_pct.toFixed(2)}%`;
    priceBadge.classList.add(up ? 'up' : 'down');

    // Gauge
    animateGauge(card, data.composite_score, data.signal, data.confidence);

    // Sparkline
    const sparkColor = up ? SCORE_COLORS.high : SCORE_COLORS.low;
    renderSparkline(`spark-${ticker}`, data.sparkline, sparkColor);

    // Indicators
    const indicators = card.querySelector('.card-indicators');
    const allScores = { ...data.technical, ...data.fundamental };
    let html = '';
    for (const [key, value] of Object.entries(allScores)) {
        const color = scoreColor(value);
        const shortLabel = key.replace('golden_death_cross', 'GD Cross')
                              .replace('earnings_surprise', 'Earnings')
                              .replace('volume_trend', 'Volume')
                              .replace('pe_ratio', 'P/E')
                              .replace('revenue_growth', 'Rev Grw')
                              .replace('earnings_growth', 'EPS Grw')
                              .replace('bollinger', 'BBands')
                              .replace('macd', 'MACD')
                              .replace('rsi', 'RSI');
        html += `
            <div class="indicator-row">
                <span class="indicator-label">${shortLabel}</span>
                <div class="indicator-bar">
                    <div class="indicator-bar-fill" style="background: ${color};" data-width="${value * 100}"></div>
                </div>
                <span class="indicator-value" style="color: ${color}">${value.toFixed(2)}</span>
            </div>
        `;
    }
    indicators.innerHTML = html;

    // Animate indicator bars
    requestAnimationFrame(() => {
        indicators.querySelectorAll('.indicator-bar-fill').forEach(fill => {
            fill.style.width = fill.dataset.width + '%';
        });
    });

    // Dip alerts
    const footer = card.querySelector('.card-footer');
    let dipHtml = '';
    if (data.dips.daily_dip) {
        dipHtml += `<span class="dip-alert"><span class="dip-icon">&#x26A0;</span> Daily ${data.dips.daily_change_pct}%</span>`;
    }
    if (data.dips.weekly_dip) {
        dipHtml += `<span class="dip-alert"><span class="dip-icon">&#x26A0;</span> Weekly ${data.dips.weekly_change_pct}%</span>`;
    }
    if (data.dips.major_dip_from_high) {
        dipHtml += `<span class="dip-alert"><span class="dip-icon">&#x26A0;</span> From High ${data.dips.from_high_pct}%</span>`;
    }
    footer.innerHTML = dipHtml;

    // Mark loaded with stagger
    setTimeout(() => card.classList.add('loaded'), 100);

    // Store data for modal
    card._stockData = data;
}

/* ---- LOAD ALL STOCKS ---- */
async function loadStocks() {
    const countEl = document.getElementById('stock-count');
    let loaded = 0;

    const promises = TICKERS.map(async (ticker, i) => {
        try {
            // Stagger requests slightly to avoid overwhelming
            await new Promise(r => setTimeout(r, i * 150));
            const res = await fetch(`/api/stock/${ticker}`);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            renderStockCard(ticker, data);
            loaded++;
            countEl.textContent = `${loaded}/${TICKERS.length} loaded`;
        } catch (e) {
            console.error(`Error loading ${ticker}:`, e);
            const card = document.getElementById(`card-${ticker}`);
            if (card) {
                card.classList.remove('skeleton');
                card.querySelector('.card-body').innerHTML = `<div style="color: var(--red); font-size: 0.85rem; padding: 20px;">Failed to load data</div>`;
            }
            loaded++;
            countEl.textContent = `${loaded}/${TICKERS.length} loaded`;
        }
    });

    await Promise.all(promises);
    countEl.textContent = `${TICKERS.length} stocks`;
}

/* ---- STOCK DETAIL MODAL ---- */
const modalOverlay = document.getElementById('modal-overlay');
const modalClose = document.getElementById('modal-close');
let modalChart = null;

function openModal(ticker, data) {
    const header = document.getElementById('modal-header');
    const up = data.change_pct >= 0;
    const changeColor = up ? 'text-green' : 'text-red';

    header.innerHTML = `
        <div class="modal-ticker">${ticker}</div>
        <div class="modal-price">
            $${data.price.toFixed(2)}
            <span class="${changeColor}" style="margin-left: 10px">${up ? '+' : ''}${data.change_pct.toFixed(2)}%</span>
            <span class="signal-badge ${signalClass(data.signal)}" style="margin-left: 12px">${data.signal}</span>
            <span class="confidence-label">${data.confidence} confidence</span>
        </div>
    `;

    // Load candlestick chart
    const chartContainer = document.getElementById('modal-chart');
    chartContainer.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:300px;"><div class="spinner"></div></div>';

    fetch(`/api/history/${ticker}?period=1y`)
        .then(r => r.json())
        .then(hist => {
            chartContainer.innerHTML = '';
            if (modalChart) { modalChart.destroy(); modalChart = null; }

            const options = {
                chart: {
                    type: 'candlestick',
                    height: 340,
                    background: 'transparent',
                    toolbar: { show: true, tools: { download: false } },
                    animations: { enabled: true, speed: 600 },
                },
                series: [{ name: 'Price', data: hist.ohlc }],
                xaxis: {
                    type: 'datetime',
                    labels: { style: { colors: '#8888a0', fontSize: '11px' } },
                    axisBorder: { color: 'rgba(255,255,255,0.06)' },
                    axisTicks: { color: 'rgba(255,255,255,0.06)' },
                },
                yaxis: {
                    labels: {
                        style: { colors: '#8888a0', fontSize: '11px' },
                        formatter: v => '$' + v.toFixed(0),
                    },
                    tooltip: { enabled: true },
                },
                grid: {
                    borderColor: 'rgba(255,255,255,0.04)',
                    strokeDashArray: 3,
                },
                plotOptions: {
                    candlestick: {
                        colors: { upward: '#00ff88', downward: '#ff4466' },
                        wick: { useFillColor: true },
                    },
                },
                tooltip: { theme: 'dark' },
                theme: { mode: 'dark' },
            };

            modalChart = new ApexCharts(chartContainer, options);
            modalChart.render();
        })
        .catch(() => {
            chartContainer.innerHTML = '<div style="color: #ff4466; text-align: center; padding: 40px;">Failed to load chart</div>';
        });

    // Detail breakdown
    const details = document.getElementById('modal-details');
    details.innerHTML = `
        <div class="detail-group">
            <h3>Technical</h3>
            ${Object.entries(data.technical).map(([k, v]) => `
                <div class="detail-row">
                    <span class="dlabel">${formatLabel(k)}</span>
                    <span class="dvalue" style="color: ${scoreColor(v)}">${v.toFixed(3)}</span>
                </div>
            `).join('')}
        </div>
        <div class="detail-group">
            <h3>Fundamental</h3>
            ${Object.entries(data.fundamental).map(([k, v]) => `
                <div class="detail-row">
                    <span class="dlabel">${formatLabel(k)}</span>
                    <span class="dvalue" style="color: ${scoreColor(v)}">${v.toFixed(3)}</span>
                </div>
            `).join('')}
        </div>
        <div class="detail-group">
            <h3>Macro</h3>
            ${Object.entries(data.macro).map(([k, v]) => `
                <div class="detail-row">
                    <span class="dlabel">${formatLabel(k)}</span>
                    <span class="dvalue" style="color: ${scoreColor(v)}">${v.toFixed(3)}</span>
                </div>
            `).join('')}
            <h3 style="margin-top: 18px">Dip Detection</h3>
            <div class="detail-row">
                <span class="dlabel">Daily Change</span>
                <span class="dvalue ${data.dips.daily_dip ? 'text-red' : ''}">${data.dips.daily_change_pct}%</span>
            </div>
            <div class="detail-row">
                <span class="dlabel">Weekly Change</span>
                <span class="dvalue ${data.dips.weekly_dip ? 'text-red' : ''}">${data.dips.weekly_change_pct}%</span>
            </div>
            <div class="detail-row">
                <span class="dlabel">From 52w High</span>
                <span class="dvalue ${data.dips.major_dip_from_high ? 'text-red' : ''}">${data.dips.from_high_pct}%</span>
            </div>
        </div>
    `;

    modalOverlay.classList.add('active');
    document.body.style.overflow = 'hidden';
}

function closeModal() {
    modalOverlay.classList.remove('active');
    document.body.style.overflow = '';
    if (modalChart) { modalChart.destroy(); modalChart = null; }
}

modalClose.addEventListener('click', closeModal);
modalOverlay.addEventListener('click', e => {
    if (e.target === modalOverlay) closeModal();
});
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeModal();
});

// Card click handlers
document.querySelectorAll('.stock-card').forEach(card => {
    card.addEventListener('click', () => {
        if (card._stockData) {
            openModal(card.dataset.ticker, card._stockData);
        }
    });
});

/* ---- BACKTEST ---- */
const btBtn = document.getElementById('run-backtest');
const btSelect = document.getElementById('backtest-ticker');

btBtn.addEventListener('click', async () => {
    const ticker = btSelect.value;
    const content = document.getElementById('backtest-content');
    btBtn.disabled = true;
    btBtn.innerHTML = '<span class="spinner"></span> Running...';
    content.innerHTML = '<div class="backtest-placeholder"><div class="spinner"></div>&nbsp;&nbsp;Backtesting strategies on ' + ticker + '... this may take a moment</div>';

    try {
        const res = await fetch(`/api/backtest/${ticker}`);
        const data = await res.json();
        renderBacktest(data.results, ticker);
    } catch (e) {
        content.innerHTML = `<div class="backtest-placeholder" style="color: var(--red)">Backtest failed: ${e.message}</div>`;
    } finally {
        btBtn.disabled = false;
        btBtn.textContent = 'Run Backtest';
    }
});

function renderBacktest(results, ticker) {
    const content = document.getElementById('backtest-content');
    if (!results || results.length === 0) {
        content.innerHTML = '<div class="backtest-placeholder">No backtest results — insufficient data</div>';
        return;
    }

    const winner = results[0];

    let tableHtml = `
        <table class="bt-table">
            <thead>
                <tr>
                    <th>Strategy</th>
                    <th>Return</th>
                    <th>Annual</th>
                    <th>Sharpe</th>
                    <th>Max DD</th>
                    <th>B&H</th>
                    <th>Alpha</th>
                    <th>Trades</th>
                </tr>
            </thead>
            <tbody>
    `;

    results.forEach((r, i) => {
        const retColor = r.total_return_pct > 0 ? 'text-green' : 'text-red';
        const alphaColor = r.alpha_vs_buyhold > 0 ? 'text-green' : 'text-red';
        const isWinner = i === 0;

        tableHtml += `
            <tr class="${isWinner ? 'winner' : ''}">
                <td>${r.strategy}</td>
                <td class="${retColor}">${r.total_return_pct > 0 ? '+' : ''}${r.total_return_pct.toFixed(1)}%</td>
                <td class="${retColor}">${r.annualized_return_pct > 0 ? '+' : ''}${r.annualized_return_pct.toFixed(1)}%</td>
                <td>${r.sharpe_ratio.toFixed(3)}</td>
                <td class="text-red">${r.max_drawdown_pct.toFixed(1)}%</td>
                <td>${r.buy_hold_return_pct > 0 ? '+' : ''}${r.buy_hold_return_pct.toFixed(1)}%</td>
                <td class="${alphaColor}">${r.alpha_vs_buyhold > 0 ? '+' : ''}${r.alpha_vs_buyhold.toFixed(1)}%</td>
                <td>${r.total_trades}</td>
            </tr>
        `;
    });

    tableHtml += '</tbody></table>';

    content.innerHTML = tableHtml + `
        <div class="bt-chart-row">
            <div class="bt-chart-box" id="bt-returns-chart"></div>
            <div class="bt-chart-box" id="bt-sharpe-chart"></div>
        </div>
    `;

    // Returns bar chart
    const names = results.map(r => r.strategy);
    const returns = results.map(r => r.total_return_pct);
    const colors = returns.map(v => v > 0 ? '#00ff88' : '#ff4466');

    new ApexCharts(document.getElementById('bt-returns-chart'), {
        chart: {
            type: 'bar',
            height: 250,
            background: 'transparent',
            toolbar: { show: false },
        },
        series: [{ name: 'Total Return', data: returns }],
        xaxis: {
            categories: names,
            labels: { style: { colors: '#8888a0', fontSize: '11px' }, rotate: -30 },
            axisBorder: { color: 'rgba(255,255,255,0.06)' },
        },
        yaxis: {
            labels: {
                style: { colors: '#8888a0', fontSize: '11px' },
                formatter: v => v.toFixed(0) + '%',
            },
        },
        grid: { borderColor: 'rgba(255,255,255,0.04)', strokeDashArray: 3 },
        plotOptions: {
            bar: {
                borderRadius: 4,
                columnWidth: '60%',
                distributed: true,
            },
        },
        colors,
        legend: { show: false },
        tooltip: {
            theme: 'dark',
            y: { formatter: v => v.toFixed(1) + '%' },
        },
        title: {
            text: 'Total Returns',
            style: { color: '#8888a0', fontSize: '12px', fontWeight: 600 },
        },
        theme: { mode: 'dark' },
    }).render();

    // Sharpe ratio chart
    const sharpes = results.map(r => r.sharpe_ratio);
    const sharpeColors = sharpes.map(v => v > 0.5 ? '#00d4ff' : v > 0 ? '#a78bfa' : '#ff4466');

    new ApexCharts(document.getElementById('bt-sharpe-chart'), {
        chart: {
            type: 'bar',
            height: 250,
            background: 'transparent',
            toolbar: { show: false },
        },
        series: [{ name: 'Sharpe Ratio', data: sharpes }],
        xaxis: {
            categories: names,
            labels: { style: { colors: '#8888a0', fontSize: '11px' }, rotate: -30 },
            axisBorder: { color: 'rgba(255,255,255,0.06)' },
        },
        yaxis: {
            labels: {
                style: { colors: '#8888a0', fontSize: '11px' },
                formatter: v => v.toFixed(2),
            },
        },
        grid: { borderColor: 'rgba(255,255,255,0.04)', strokeDashArray: 3 },
        plotOptions: {
            bar: {
                borderRadius: 4,
                columnWidth: '60%',
                distributed: true,
            },
        },
        colors: sharpeColors,
        legend: { show: false },
        tooltip: {
            theme: 'dark',
            y: { formatter: v => v.toFixed(3) },
        },
        title: {
            text: 'Sharpe Ratios (Risk-Adjusted)',
            style: { color: '#8888a0', fontSize: '12px', fontWeight: 600 },
        },
        theme: { mode: 'dark' },
    }).render();
}

/* ---- INIT ---- */
async function init() {
    await loadMacro();
    await loadStocks();
}

init();
