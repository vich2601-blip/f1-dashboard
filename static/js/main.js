/* ═══════════════════════════════════════════════════════════
   F1 Dashboard — Client-side Interactivity
   ═══════════════════════════════════════════════════════════ */

// ── Mobile Navigation ──────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
    const navToggle = document.getElementById('nav-toggle');
    const navLinks = document.getElementById('nav-links');
    const navbar = document.getElementById('main-nav');

    function setNavOpen(open) {
        if (navLinks) navLinks.classList.toggle('open', open);
        if (navbar) navbar.classList.toggle('open', open);
        if (navToggle) navToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    if (navToggle && navLinks) {
        navToggle.addEventListener('click', function () {
            const open = !(navbar && navbar.classList.contains('open'));
            setNavOpen(open);
        });

        navLinks.querySelectorAll('.nav-link').forEach(function (link) {
            link.addEventListener('click', function () {
                setNavOpen(false);
            });
        });
    }

    initCommandPalette();
});

function initCommandPalette() {
    var overlay = document.getElementById('cmd-overlay');
    var input = document.getElementById('cmd-input');
    var results = document.getElementById('cmd-results');
    var trigger = document.getElementById('cmd-trigger');
    var indexEl = document.getElementById('search-index');
    if (!overlay || !input || !results) return;

    var index = { drivers: [], races: [], season: new Date().getFullYear() };
    try {
        if (indexEl && indexEl.textContent) index = JSON.parse(indexEl.textContent);
    } catch (err) { /* ignore */ }

    var pages = [
        { label: 'Overview', href: '/', hint: 'Home' },
        { label: 'Live timing', href: '/live', hint: 'Session' },
        { label: 'Calendar', href: '/calendar', hint: 'Schedule' },
        { label: 'Standings', href: '/standings', hint: 'Championship' },
        { label: 'Compare drivers', href: '/compare', hint: 'Head to head' },
        { label: 'Win predictor', href: '/predict', hint: 'AI' }
    ];

    var items = pages.slice();
    (index.drivers || []).forEach(function (d) {
        items.push({
            label: d.name + ' (' + d.code + ')',
            href: '/driver/' + d.code,
            hint: d.team || 'Driver'
        });
    });
    (index.races || []).forEach(function (r) {
        items.push({
            label: r.name,
            href: '/race/' + index.season + '/' + r.round,
            hint: 'R' + r.round
        });
    });

    var filtered = items;
    var selected = 0;

    function render() {
        results.innerHTML = '';
        filtered.slice(0, 12).forEach(function (item, i) {
            var li = document.createElement('li');
            li.className = 'cmd-item';
            li.setAttribute('aria-selected', i === selected ? 'true' : 'false');
            li.innerHTML = item.label + '<span>' + item.hint + '</span>';
            li.addEventListener('mousedown', function (e) {
                e.preventDefault();
                window.location.href = item.href;
            });
            results.appendChild(li);
        });
    }

    function openPalette() {
        overlay.hidden = false;
        input.value = '';
        filtered = items;
        selected = 0;
        render();
        setTimeout(function () { input.focus(); }, 0);
    }

    function closePalette() {
        overlay.hidden = true;
        input.blur();
    }

    function applyFilter() {
        var q = input.value.trim().toLowerCase();
        filtered = !q ? items : items.filter(function (item) {
            return (item.label + ' ' + item.hint).toLowerCase().indexOf(q) !== -1;
        });
        selected = 0;
        render();
    }

    if (trigger) trigger.addEventListener('click', openPalette);
    overlay.addEventListener('click', function (e) {
        if (e.target === overlay) closePalette();
    });
    input.addEventListener('input', applyFilter);
    input.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { closePalette(); }
        if (e.key === 'ArrowDown') { e.preventDefault(); selected = Math.min(selected + 1, Math.min(filtered.length, 12) - 1); render(); }
        if (e.key === 'ArrowUp') { e.preventDefault(); selected = Math.max(selected - 1, 0); render(); }
        if (e.key === 'Enter' && filtered[selected]) { window.location.href = filtered[selected].href; }
    });

    document.addEventListener('keydown', function (e) {
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
            e.preventDefault();
            if (overlay.hidden) openPalette(); else closePalette();
        }
    });
}

// ── Countdown Timer ────────────────────────────────────────
(function initCountdown() {
    const el = document.getElementById('countdown');
    if (!el) return;

    const target = el.dataset.target;
    if (!target) return;

    const targetDate = new Date(target);
    var intervalId = null;

    function update() {
        const now = new Date();
        const diff = targetDate - now;

        if (diff <= 0) {
            document.getElementById('countdown-days').textContent = '0';
            document.getElementById('countdown-hours').textContent = '0';
            document.getElementById('countdown-minutes').textContent = '0';
            document.getElementById('countdown-seconds').textContent = '0';
            // Fix #6: Clear interval when countdown reaches zero
            if (intervalId !== null) {
                clearInterval(intervalId);
                intervalId = null;
            }
            return;
        }

        const days = Math.floor(diff / (1000 * 60 * 60 * 24));
        const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
        const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
        const seconds = Math.floor((diff % (1000 * 60)) / 1000);

        const daysEl = document.getElementById('countdown-days');
        const hoursEl = document.getElementById('countdown-hours');
        const minutesEl = document.getElementById('countdown-minutes');
        const secondsEl = document.getElementById('countdown-seconds');

        if (daysEl) daysEl.textContent = days;
        if (hoursEl) hoursEl.textContent = hours;
        if (minutesEl) minutesEl.textContent = minutes;
        if (secondsEl) secondsEl.textContent = seconds;
    }

    update();
    intervalId = setInterval(update, 1000);
})();

// ── Chart.js Defaults ──────────────────────────────────────
if (typeof Chart !== 'undefined') {
    Chart.defaults.color = '#b0b0b8';
    Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.06)';
    Chart.defaults.font.family = "'Outfit', system-ui, sans-serif";
}

// ── Horizontal Points Bar Chart ────────────────────────────
function initPointsChart(canvasId, data, title) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') return;

    new Chart(canvas, {
        type: 'bar',
        data: {
            labels: data.labels,
            datasets: [{
                label: title,
                data: data.points,
                backgroundColor: data.colours.map(function (c) { return c + '44'; }),
                borderColor: data.colours,
                borderWidth: 1,
                borderRadius: 4,
                barThickness: 20,
            }]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(31, 31, 39, 0.95)',
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                    borderWidth: 1,
                    cornerRadius: 8,
                    padding: 12,
                    titleFont: { family: "'Inter', sans-serif", weight: '600' },
                    bodyFont: { family: "'IBM Plex Mono', monospace" },
                },
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: { font: { family: "'IBM Plex Mono', monospace", size: 11 } },
                },
                y: {
                    grid: { display: false },
                    ticks: { font: { family: "'IBM Plex Mono', monospace", size: 12, weight: '600' } },
                },
            },
            animation: {
                duration: 1200,
                easing: 'easeOutQuart',
            },
        },
    });
}

// ── Lap Time Line Chart ────────────────────────────────────
function initLapTimeChart(lapData) {
    const canvas = document.getElementById('lapTimeChart');
    if (!canvas || typeof Chart === 'undefined') return;

    function timeToSeconds(timeStr) {
        if (!timeStr) return null;
        var parts = timeStr.split(':').map(parseFloat);
        return parts.reduce(function (acc, p) { return acc * 60 + p; }, 0);
    }

    var datasets = [];
    var maxLap = 0;
    var allTimes = [];

    Object.keys(lapData).forEach(function (driverCode) {
        var driver = lapData[driverCode];
        var lapTimes = driver.laps.map(function (lap) {
            if (lap.lap > maxLap) maxLap = lap.lap;
            return {
                x: lap.lap,
                y: timeToSeconds(lap.time),
            };
        }).filter(function (d) { return d.y !== null && d.y > 0; });
        lapTimes.forEach(function (d) { allTimes.push(d.y); });

        datasets.push({
            label: driverCode,
            data: lapTimes,
            borderColor: driver.colour,
            backgroundColor: driver.colour + '22',
            borderWidth: 1.5,
            pointRadius: 0,
            pointHoverRadius: 4,
            tension: 0.3,
            fill: false,
        });
    });

    var labels = [];
    for (var i = 1; i <= maxLap; i++) labels.push(i);

    // Focus the axis on racing pace: safety-car, pit and red-flag laps can be
    // minutes long and would otherwise flatten every other lap into a line.
    allTimes.sort(function (a, b) { return a - b; });
    var median = allTimes.length ? allTimes[Math.floor(allTimes.length / 2)] : 0;
    var yMin = allTimes.length ? Math.floor(allTimes[0] - 0.5) : undefined;
    var yMax = median ? Math.ceil(median * 1.08) : undefined;

    new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels,
            datasets: datasets,
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false,
            },
            plugins: {
                legend: {
                    labels: {
                        usePointStyle: true,
                        pointStyle: 'line',
                        font: { family: "'IBM Plex Mono', monospace", size: 11 },
                    },
                },
                tooltip: {
                    backgroundColor: 'rgba(31, 31, 39, 0.95)',
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                    borderWidth: 1,
                    cornerRadius: 8,
                    padding: 12,
                    callbacks: {
                        label: function (context) {
                            var secs = context.parsed.y;
                            if (secs === null) return '';
                            var mins = Math.floor(secs / 60);
                            var rem = (secs % 60).toFixed(3);
                            return context.dataset.label + ': ' + mins + ':' + (rem < 10 ? '0' : '') + rem;
                        },
                    },
                },
            },
            scales: {
                x: {
                    title: { display: true, text: 'Lap', font: { weight: '600' } },
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: { font: { family: "'IBM Plex Mono', monospace", size: 10 } },
                },
                y: {
                    min: yMin,
                    max: yMax,
                    title: { display: true, text: 'Lap time (slow SC / pit laps clipped)', font: { weight: '600' } },
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: {
                        font: { family: "'IBM Plex Mono', monospace", size: 10 },
                        callback: function (value) {
                            var mins = Math.floor(value / 60);
                            var secs = Math.round(value % 60);
                            return mins + ':' + (secs < 10 ? '0' : '') + secs;
                        },
                    },
                },
            },
            animation: {
                duration: 1500,
                easing: 'easeOutQuart',
            },
        },
    });
}

// ── Position Change Chart (Spaghetti) ──────────────────────
function initPositionChart(posData) {
    var canvas = document.getElementById('positionChart');
    if (!canvas || typeof Chart === 'undefined') return;

    var datasets = [];
    var maxLap = 0;

    Object.keys(posData).forEach(function (driverCode) {
        var driver = posData[driverCode];
        var positions = driver.positions.map(function (p) {
            if (p.lap > maxLap) maxLap = p.lap;
            return { x: p.lap, y: p.position };
        });

        datasets.push({
            label: driverCode,
            data: positions,
            borderColor: driver.colour,
            backgroundColor: driver.colour + '22',
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 5,
            tension: 0.2,
            fill: false,
        });
    });

    var labels = [];
    for (var i = 1; i <= maxLap; i++) labels.push(i);

    new Chart(canvas, {
        type: 'line',
        data: { labels: labels, datasets: datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    labels: {
                        usePointStyle: true,
                        pointStyle: 'line',
                        font: { family: "'IBM Plex Mono', monospace", size: 11 },
                    },
                },
                tooltip: {
                    backgroundColor: 'rgba(31, 31, 39, 0.95)',
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                    borderWidth: 1,
                    cornerRadius: 8,
                    padding: 12,
                    callbacks: {
                        label: function (ctx) {
                            return ctx.dataset.label + ': P' + ctx.parsed.y;
                        },
                    },
                },
            },
            scales: {
                x: {
                    title: { display: true, text: 'Lap', font: { weight: '600' } },
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: { font: { family: "'IBM Plex Mono', monospace", size: 10 } },
                },
                y: {
                    title: { display: true, text: 'Position', font: { weight: '600' } },
                    reverse: true,
                    min: 1,
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: {
                        font: { family: "'IBM Plex Mono', monospace", size: 10 },
                        stepSize: 1,
                        callback: function (v) { return 'P' + v; },
                    },
                },
            },
            animation: { duration: 1500, easing: 'easeOutQuart' },
        },
    });
}

// ── Tyre Strategy Timeline ─────────────────────────────────
function initTyreTimeline(stintData, totalLaps) {
    var container = document.getElementById('tyre-timeline');
    if (!container || !stintData) return;

    var COMPOUND_COLOURS = {
        'SOFT': '#ef4444',
        'MEDIUM': '#eab308',
        'HARD': '#f5f5f5',
        'INTERMEDIATE': '#22c55e',
        'WET': '#3b82f6',
    };

    var html = '';
    Object.keys(stintData).forEach(function (driverCode) {
        var driver = stintData[driverCode];
        html += '<div class="tyre-row">';
        html += '<div class="tyre-driver"><span class="tyre-colour-dot" style="background:' + driver.colour + '"></span>';
        html += '<span class="mono">' + driverCode + '</span></div>';
        html += '<div class="tyre-bar-track">';

        driver.stints.forEach(function (stint) {
            var startPct = ((stint.start - 1) / totalLaps * 100).toFixed(1);
            var widthPct = ((stint.end - stint.start + 1) / totalLaps * 100).toFixed(1);
            var bgColour = COMPOUND_COLOURS[stint.compound] || '#888';
            var textColour = stint.compound === 'HARD' ? '#111' : '#fff';

            html += '<div class="tyre-stint" style="left:' + startPct + '%;width:' + widthPct + '%;background:' + bgColour + ';color:' + textColour + '" title="' + stint.compound + ' (Lap ' + stint.start + '-' + stint.end + ')">';
            if (parseFloat(widthPct) > 8) {
                html += '<span class="tyre-stint-label">' + stint.compound.charAt(0) + '</span>';
            }
            html += '</div>';
        });

        html += '</div></div>';
    });

    container.innerHTML = html;
}

// ── Driver Comparison Bar Chart ────────────────────────────
function initComparisonChart(canvasId, data) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === 'undefined') return;

    new Chart(canvas, {
        type: 'bar',
        data: {
            labels: data.labels,
            datasets: [
                {
                    label: data.driver1.name,
                    data: data.driver1.values,
                    backgroundColor: data.driver1.colour + '88',
                    borderColor: data.driver1.colour,
                    borderWidth: 1,
                    borderRadius: 4,
                },
                {
                    label: data.driver2.name,
                    data: data.driver2.values,
                    backgroundColor: data.driver2.colour + '88',
                    borderColor: data.driver2.colour,
                    borderWidth: 1,
                    borderRadius: 4,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: {
                        font: { family: "'IBM Plex Mono', monospace", size: 12 },
                        usePointStyle: true,
                    },
                },
                tooltip: {
                    backgroundColor: 'rgba(31, 31, 39, 0.95)',
                    borderColor: 'rgba(255, 255, 255, 0.1)',
                    borderWidth: 1,
                    cornerRadius: 8,
                    padding: 12,
                },
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { font: { family: "'IBM Plex Mono', monospace", size: 11, weight: '600' } },
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: { font: { family: "'IBM Plex Mono', monospace", size: 11 } },
                },
            },
            animation: { duration: 1200, easing: 'easeOutQuart' },
        },
    });
}

// ══════════════════════════════════════════════════════════════
// LIVE TIMING — WebSocket Client & Interactive UI
// ══════════════════════════════════════════════════════════════

// ── Global WebSocket Connection ────────────────────────────
var _socket = null;
var _selectedDriver = null;
var _driverMapGlobal = {};
var _speedTraceData = [];
var _lastPositions = {};
var _lastLaps = {};
var _lastIntervals = {};
var _lastStints = {};
var _sessionYear = null;

// ── WebSocket Connection Status ────────────────────────────
(function initGlobalWebSocket() {
    if (typeof io === 'undefined') return;

    _socket = io({
        transports: ['websocket', 'polling'],
        reconnectionAttempts: Infinity,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 5000,
    });

    var dot = document.getElementById('ws-dot');
    var label = document.getElementById('ws-label');
    var statusEl = document.getElementById('ws-status');

    _socket.on('connect', function() {
        if (dot) dot.className = 'ws-dot ws-dot-connected';
        if (label) label.textContent = 'LIVE';
        if (statusEl) {
            statusEl.title = 'WebSocket connected';
            statusEl.classList.add('connected');
        }
        console.log('[WS] Connected');
    });

    _socket.on('disconnect', function() {
        if (dot) dot.className = 'ws-dot ws-dot-disconnected';
        if (label) label.textContent = 'OFFLINE';
        if (statusEl) {
            statusEl.title = 'WebSocket disconnected';
            statusEl.classList.remove('connected');
        }
        console.log('[WS] Disconnected');
    });

    _socket.on('reconnecting', function() {
        if (dot) dot.className = 'ws-dot ws-dot-reconnecting';
        if (label) label.textContent = 'CONNECTING';
        if (statusEl) statusEl.title = 'Reconnecting...';
    });

    _socket.on('connection_status', function(data) {
        console.log('[WS] Connection status:', data);
    });
})();


// ── Initialize Live WebSocket for a Session ────────────────
function initLiveWebSocket(sessionKey, driverMap, sessionYear) {
    if (!_socket || !sessionKey) return;

    _driverMapGlobal = driverMap;
    _sessionYear = sessionYear;

    // Join the session room for push updates
    _socket.emit('join_session', { session_key: sessionKey });

    // Handle real-time position updates (every ~2s)
    _socket.on('positions_update', function(data) {
        _flashUpdateIndicator();
        if (data.positions) _updateTimingTower(data.positions, data.intervals);
        if (data.locations && typeof updateCarPositions === 'function') {
            updateCarPositions(data.locations);
        }
    });

    // Handle full data updates (every ~30s)
    _socket.on('full_update', function(data) {
        if (data.laps) _updateLapData(data.laps);
        if (data.weather) _updateWeather(data.weather);
        if (data.stints) _updateStints(data.stints);
        if (data.race_control) _checkNewRaceControl(data.race_control);
    });

    // Handle telemetry responses
    _socket.on('telemetry_data', function(data) {
        if (data.telemetry && data.telemetry.length > 0) {
            _renderSpeedTrace(data.telemetry, data.driver);
            _animateTelemetryBars(data.telemetry);
        }
    });

    console.log('[WS] Live WebSocket initialized for session', sessionKey);
}

// ── Session Picker Navigation ──────────────────────────────
function changeYear(year) {
    window.location.href = '/live?year=' + year;
}

function changeSession(sessionKey) {
    var yearSelect = document.getElementById('year-select');
    var year = yearSelect ? yearSelect.value : 2026;
    window.location.href = '/live?year=' + year + '&session=' + sessionKey;
}

// ── Flash update indicator ─────────────────────────────────
function _flashUpdateIndicator() {
    var tick = document.getElementById('tower-update-tick');
    if (!tick) return;
    tick.classList.add('flash');
    setTimeout(function() { tick.classList.remove('flash'); }, 600);
}

// ══════════════════════════════════════════════════════════════
// TIMING TOWER — Animated Position Updates
// ══════════════════════════════════════════════════════════════

function _updateTimingTower(positions, intervals) {
    var tower = document.getElementById('tower-list');
    if (!tower) return;

    // Build interval lookup
    var intervalMap = {};
    if (intervals) {
        for (var i = 0; i < intervals.length; i++) {
            var iv = intervals[i];
            intervalMap[iv.driver_number] = iv;
        }
    }

    // Sort positions
    positions.sort(function(a, b) { return (a.position || 99) - (b.position || 99); });

    for (var p = 0; p < positions.length; p++) {
        var pos = positions[p];
        var dn = pos.driver_number;
        var driver = _driverMapGlobal[dn] || {};
        var row = document.getElementById('tower-row-' + dn);

        if (!row) {
            // Create new row for this driver
            row = document.createElement('div');
            row.className = 'tower-row';
            row.id = 'tower-row-' + dn;
            row.setAttribute('data-driver', dn);
            row.setAttribute('data-acronym', driver.name_acronym || dn);
            row.onclick = (function(d) { return function() { selectDriver(d); }; })(dn);

            var colour = driver.team_colour || '888';
            var acronym = driver.name_acronym || dn;

            row.innerHTML = '<div class="tower-pos"><span class="tower-pos-num mono">' + pos.position + '</span></div>' +
                '<div class="tower-driver"><div class="tower-team-bar" style="background: #' + colour + '"></div>' +
                '<span class="tower-code mono">' + acronym + '</span></div>' +
                '<div class="tower-sectors"><span class="sector-time mono">—</span><span class="sector-time mono">—</span><span class="sector-time mono">—</span></div>' +
                '<div class="tower-gap mono">—</div>';
            tower.appendChild(row);
        }

        // Update position number
        var posEl = row.querySelector('.tower-pos-num');
        if (posEl) {
            var oldPos = parseInt(posEl.textContent);
            posEl.textContent = pos.position;
            posEl.className = 'tower-pos-num mono' + (pos.position <= 3 ? ' tower-pos-podium' : '');

            // Animate position change
            if (oldPos && oldPos !== pos.position) {
                row.classList.add(pos.position < oldPos ? 'tower-row-up' : 'tower-row-down');
                setTimeout(function(r) {
                    return function() { r.classList.remove('tower-row-up', 'tower-row-down'); };
                }(row), 800);
            }
        }

        // Update gap
        var gapEl = row.querySelector('.tower-gap');
        if (gapEl && intervalMap[dn]) {
            var gap = intervalMap[dn].gap_to_leader || intervalMap[dn].interval || '';
            gapEl.textContent = pos.position === 1 ? 'LEADER' : gap || '—';
        }

        // Reorder DOM to match position order
        if (row.parentNode === tower) {
            var expectedIndex = p;
            var currentIndex = Array.from(tower.children).indexOf(row);
            if (currentIndex !== expectedIndex && expectedIndex < tower.children.length) {
                tower.insertBefore(row, tower.children[expectedIndex]);
            }
        }

        // Update selected driver detail if it matches
        if (_selectedDriver === dn) {
            _updateDriverDetail(dn, pos, intervalMap[dn]);
        }
    }

    _lastPositions = {};
    for (var j = 0; j < positions.length; j++) {
        _lastPositions[positions[j].driver_number] = positions[j];
    }
}

// ── Update lap sector display on tower rows ────────────────
function _updateLapData(laps) {
    if (!laps || !laps.length) return;

    // Get latest lap per driver
    var latestLaps = {};
    var bestS1 = null, bestS2 = null, bestS3 = null;
    for (var i = 0; i < laps.length; i++) {
        var lap = laps[i];
        var dn = lap.driver_number;
        if (!dn) continue;
        if (!latestLaps[dn] || (lap.lap_number || 0) > (latestLaps[dn].lap_number || 0)) {
            latestLaps[dn] = lap;
        }
        if (lap.duration_sector_1 && (bestS1 === null || lap.duration_sector_1 < bestS1)) bestS1 = lap.duration_sector_1;
        if (lap.duration_sector_2 && (bestS2 === null || lap.duration_sector_2 < bestS2)) bestS2 = lap.duration_sector_2;
        if (lap.duration_sector_3 && (bestS3 === null || lap.duration_sector_3 < bestS3)) bestS3 = lap.duration_sector_3;
    }

    _lastLaps = latestLaps;

    // Update tower rows
    for (var driverNum in latestLaps) {
        var l = latestLaps[driverNum];
        var row = document.getElementById('tower-row-' + driverNum);
        if (!row) continue;

        var sectors = row.querySelectorAll('.sector-time');
        if (sectors.length >= 3) {
            _updateSectorCell(sectors[0], l.duration_sector_1, bestS1);
            _updateSectorCell(sectors[1], l.duration_sector_2, bestS2);
            _updateSectorCell(sectors[2], l.duration_sector_3, bestS3);
        }

        var gapEl = row.querySelector('.tower-gap');
        if (gapEl && l.lap_duration) {
            gapEl.textContent = l.lap_duration.toFixed(3);
        }
    }
}

function _updateSectorCell(el, value, sessionBest) {
    if (!el) return;
    if (value) {
        el.textContent = value.toFixed(3);
        el.className = 'sector-time mono';
        if (Math.abs(value - sessionBest) < 0.001) {
            el.classList.add('sector-purple');
        } else {
            el.classList.add('sector-green');
        }
    } else {
        el.textContent = '—';
        el.className = 'sector-time mono';
    }
}

// ── Update Weather ─────────────────────────────────────────
function _updateWeather(weather) {
    if (!weather) return;
    _setTextContent('weather-track', weather.track_temperature ? weather.track_temperature.toFixed(1) + '°C' : '—');
    _setTextContent('weather-air', weather.air_temperature ? weather.air_temperature.toFixed(1) + '°C' : '—');
    _setTextContent('weather-humidity', weather.humidity ? Math.round(weather.humidity) + '%' : '—');
    _setTextContent('weather-wind', weather.wind_speed ? weather.wind_speed.toFixed(1) + ' km/h' : '—');
    _setTextContent('weather-rain', weather.rainfall ? 'Yes' : 'No');
    _setTextContent('weather-pressure', weather.pressure ? weather.pressure.toFixed(1) + ' hPa' : '—');
}

function _setTextContent(id, text) {
    var el = document.getElementById(id);
    if (el) el.textContent = text;
}

// ── Update Stints ──────────────────────────────────────────
function _updateStints(stints) {
    if (!stints) return;
    var latestStints = {};
    for (var i = 0; i < stints.length; i++) {
        var s = stints[i];
        var dn = s.driver_number;
        if (!dn) continue;
        if (!latestStints[dn] || (s.stint_number || 0) > (latestStints[dn].stint_number || 0)) {
            latestStints[dn] = s;
        }
    }
    _lastStints = latestStints;
}

// ══════════════════════════════════════════════════════════════
// RACE CONTROL TOAST NOTIFICATIONS
// ══════════════════════════════════════════════════════════════

var _lastRaceControlCount = 0;

function _checkNewRaceControl(messages) {
    if (!messages || !messages.length) return;

    // On first load, just record the count
    if (_lastRaceControlCount === 0) {
        _lastRaceControlCount = messages.length;
        return;
    }

    // Show toasts for new messages
    var newMessages = messages.slice(_lastRaceControlCount);
    _lastRaceControlCount = messages.length;

    for (var i = 0; i < newMessages.length; i++) {
        _showRCToast(newMessages[i]);
    }

    // Also update the race control feed
    var feed = document.getElementById('rc-feed');
    if (feed) {
        var html = '';
        var displayMsgs = messages.slice(-10).reverse();
        for (var j = 0; j < displayMsgs.length; j++) {
            var msg = displayMsgs[j];
            var flagClass = '';
            if (msg.flag) {
                var f = msg.flag.toUpperCase();
                if (f.indexOf('RED') >= 0) flagClass = 'rc-red';
                else if (f.indexOf('YELLOW') >= 0) flagClass = 'rc-yellow';
                else if (f.indexOf('GREEN') >= 0) flagClass = 'rc-green';
            }
            html += '<div class="rc-message ' + flagClass + '">';
            html += '<span class="rc-category">' + (msg.category || '') + '</span>';
            html += '<span class="rc-text">' + (msg.message || '') + '</span>';
            html += '</div>';
        }
        feed.innerHTML = html || '<p class="info-empty">No race control messages</p>';
    }
}

function _showRCToast(msg) {
    var container = document.getElementById('rc-toast-container');
    if (!container) return;

    var toast = document.createElement('div');
    toast.className = 'rc-toast';

    // Determine toast color based on flag
    if (msg.flag) {
        var flag = msg.flag.toUpperCase();
        if (flag.indexOf('RED') >= 0) toast.classList.add('rc-toast-red');
        else if (flag.indexOf('YELLOW') >= 0) toast.classList.add('rc-toast-yellow');
        else if (flag.indexOf('GREEN') >= 0) toast.classList.add('rc-toast-green');
        else if (flag.indexOf('CHECKERED') >= 0 || flag.indexOf('CHEQUERED') >= 0) toast.classList.add('rc-toast-checkered');
    }

    toast.innerHTML = '<div class="rc-toast-header">' +
        '<span class="rc-toast-flag">' + (msg.flag ? '🚩 ' + msg.flag : '📢') + '</span>' +
        '<span class="rc-toast-category mono">' + (msg.category || 'RC') + '</span>' +
        '</div>' +
        '<div class="rc-toast-message">' + (msg.message || '') + '</div>';

    container.appendChild(toast);

    // Animate in
    requestAnimationFrame(function() {
        toast.classList.add('rc-toast-visible');
    });

    // Auto-dismiss after 8 seconds
    setTimeout(function() {
        toast.classList.remove('rc-toast-visible');
        toast.classList.add('rc-toast-exit');
        setTimeout(function() {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 500);
    }, 8000);
}


// ══════════════════════════════════════════════════════════════
// DRIVER SELECTION & TELEMETRY DETAIL PANEL
// ══════════════════════════════════════════════════════════════

function selectDriver(driverNumber) {
    _selectedDriver = driverNumber;
    var driver = _driverMapGlobal[driverNumber] || {};

    // Show driver detail card
    var card = document.getElementById('driver-detail-card');
    if (card) {
        card.style.display = 'block';
        card.classList.add('driver-detail-show');
    }

    // Show speed trace panel
    var tracePanel = document.getElementById('speed-trace-panel');
    if (tracePanel) tracePanel.style.display = 'block';

    // Update driver info
    _setTextContent('driver-detail-code', driver.name_acronym || driverNumber);
    _setTextContent('driver-detail-team', driver.team_name || '—');
    var teamBar = document.getElementById('driver-detail-team-bar');
    if (teamBar) teamBar.style.background = '#' + (driver.team_colour || '888');

    // Update title
    _setTextContent('speed-trace-title', (driver.name_acronym || 'Driver') + ' — Speed Trace');

    // Update position/gap from last known data
    var pos = _lastPositions[driverNumber];
    if (pos) _updateDriverDetail(driverNumber, pos, null);

    // Update stint info
    var stint = _lastStints[driverNumber];
    if (stint) {
        var tyreDot = document.getElementById('detail-tyre-dot');
        if (tyreDot) tyreDot.className = 'tyre-dot tyre-' + (stint.compound || '').toLowerCase();
        _setTextContent('detail-tyre-compound', stint.compound || '—');
        _setTextContent('detail-tyre-age', stint.tyre_age_at_start ? 'Lap ' + stint.lap_start : '—');
    }

    // Highlight selected row in tower
    var rows = document.querySelectorAll('.tower-row');
    for (var i = 0; i < rows.length; i++) {
        rows[i].classList.toggle('tower-row-selected', parseInt(rows[i].getAttribute('data-driver')) === driverNumber);
    }

    // Request telemetry via WebSocket (if we have session info)
    if (_socket && typeof SESSION_KEY !== 'undefined') {
        var lap = _lastLaps[driverNumber];
        var lapNum = lap ? lap.lap_number : null;
        if (lapNum) {
            _socket.emit('request_telemetry', {
                year: _sessionYear,
                round: null, // Will be resolved server-side from session_key
                driver: driver.name_acronym || String(driverNumber),
                lap: lapNum,
            });
        }
    }
}

function deselectDriver() {
    _selectedDriver = null;
    var card = document.getElementById('driver-detail-card');
    if (card) {
        card.classList.remove('driver-detail-show');
        setTimeout(function() { card.style.display = 'none'; }, 300);
    }

    var rows = document.querySelectorAll('.tower-row');
    for (var i = 0; i < rows.length; i++) {
        rows[i].classList.remove('tower-row-selected');
    }
}

function closeSpeedTrace() {
    var panel = document.getElementById('speed-trace-panel');
    if (panel) panel.style.display = 'none';
    deselectDriver();
}

function _updateDriverDetail(driverNumber, pos, interval) {
    _setTextContent('detail-position', 'P' + (pos.position || '—'));
    var lap = _lastLaps[driverNumber];
    if (lap && lap.lap_duration) {
        _setTextContent('detail-last-lap', _formatLapTime(lap.lap_duration));
    }
    if (interval) {
        _setTextContent('detail-gap', pos.position === 1 ? 'LEADER' : (interval.gap_to_leader || '—'));
    }
}

function _formatLapTime(secs) {
    if (!secs || secs <= 0) return '—';
    var mins = Math.floor(secs / 60);
    var rem = (secs % 60).toFixed(3);
    if (rem < 10) rem = '0' + rem;
    return mins + ':' + rem;
}


// ══════════════════════════════════════════════════════════════
// SPEED TRACE CANVAS RENDERER
// ══════════════════════════════════════════════════════════════

function _renderSpeedTrace(telemetry, driverCode) {
    var canvas = document.getElementById('speed-trace-canvas');
    if (!canvas || !telemetry || telemetry.length === 0) return;

    var ctx = canvas.getContext('2d');
    var container = canvas.parentElement;
    canvas.width = container.clientWidth;
    canvas.height = 200;

    var w = canvas.width;
    var h = canvas.height;
    var padding = { top: 10, bottom: 30, left: 50, right: 20 };

    // Extract data
    var distances = telemetry.map(function(t) { return t.distance; });
    var speeds = telemetry.map(function(t) { return t.speed; });
    var throttles = telemetry.map(function(t) { return t.throttle; });
    var brakes = telemetry.map(function(t) { return t.brake; });

    var maxDist = Math.max.apply(null, distances) || 1;
    var maxSpeed = Math.max.apply(null, speeds) || 350;

    ctx.clearRect(0, 0, w, h);

    // Draw grid
    ctx.strokeStyle = 'rgba(255,255,255,0.06)';
    ctx.lineWidth = 1;
    for (var spd = 50; spd <= maxSpeed; spd += 50) {
        var gy = padding.top + (1 - spd / maxSpeed) * (h - padding.top - padding.bottom);
        ctx.beginPath();
        ctx.moveTo(padding.left, gy);
        ctx.lineTo(w - padding.right, gy);
        ctx.stroke();
        ctx.fillStyle = 'rgba(255,255,255,0.3)';
        ctx.font = "10px 'IBM Plex Mono', monospace";
        ctx.textAlign = 'right';
        ctx.fillText(spd + '', padding.left - 5, gy + 4);
    }

    // Draw brake zones (background fill)
    ctx.fillStyle = 'rgba(239, 68, 68, 0.15)';
    for (var b = 0; b < brakes.length; b++) {
        if (brakes[b] > 0) {
            var bx = padding.left + (distances[b] / maxDist) * (w - padding.left - padding.right);
            ctx.fillRect(bx, padding.top, 3, h - padding.top - padding.bottom);
        }
    }

    // Draw throttle zones (background fill)
    ctx.fillStyle = 'rgba(34, 197, 94, 0.08)';
    for (var t = 0; t < throttles.length; t++) {
        if (throttles[t] > 90) {
            var tx = padding.left + (distances[t] / maxDist) * (w - padding.left - padding.right);
            ctx.fillRect(tx, padding.top, 3, h - padding.top - padding.bottom);
        }
    }

    // Draw speed line
    var driver = _driverMapGlobal[_selectedDriver] || {};
    var lineColor = driver.team_colour ? '#' + driver.team_colour : '#22c55e';

    ctx.beginPath();
    ctx.strokeStyle = lineColor;
    ctx.lineWidth = 2;
    ctx.lineJoin = 'round';

    for (var s = 0; s < speeds.length; s++) {
        var sx = padding.left + (distances[s] / maxDist) * (w - padding.left - padding.right);
        var sy = padding.top + (1 - speeds[s] / maxSpeed) * (h - padding.top - padding.bottom);
        if (s === 0) ctx.moveTo(sx, sy);
        else ctx.lineTo(sx, sy);
    }
    ctx.stroke();

    // Gradient fill under speed line
    var gradient = ctx.createLinearGradient(0, padding.top, 0, h - padding.bottom);
    gradient.addColorStop(0, lineColor + '33');
    gradient.addColorStop(1, lineColor + '00');
    ctx.lineTo(w - padding.right, h - padding.bottom);
    ctx.lineTo(padding.left, h - padding.bottom);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    // X-axis label
    ctx.fillStyle = 'rgba(255,255,255,0.4)';
    ctx.font = "10px 'IBM Plex Mono', monospace";
    ctx.textAlign = 'center';
    ctx.fillText('Distance (m)', w / 2, h - 5);

    // Store for animation
    _speedTraceData = telemetry;
}

// ── Animate telemetry bars through the lap ─────────────────
function _animateTelemetryBars(telemetry) {
    if (!telemetry || telemetry.length === 0) return;

    var index = 0;
    var frameTime = 50; // ms per frame — full lap in ~10 seconds

    function tick() {
        if (index >= telemetry.length) index = 0; // loop

        var point = telemetry[index];

        // Throttle bar
        var throttleBar = document.getElementById('throttle-bar');
        if (throttleBar) throttleBar.style.width = point.throttle + '%';
        _setTextContent('throttle-value', Math.round(point.throttle) + '%');

        // Brake bar
        var brakeBar = document.getElementById('brake-bar');
        if (brakeBar) brakeBar.style.width = (point.brake * 100) + '%';
        _setTextContent('brake-value', Math.round(point.brake * 100) + '%');

        // Gear
        _setTextContent('gear-num', point.gear === 0 ? 'N' : point.gear);

        // DRS
        var drsEl = document.getElementById('drs-indicator');
        var drsOpen = point.drs >= 10;
        _setTextContent('drs-status', drsOpen ? 'OPEN' : 'OFF');
        if (drsEl) drsEl.className = 'drs-indicator' + (drsOpen ? ' drs-open' : '');

        // Speed gauge
        _updateSpeedGauge(point.speed);

        index++;
        if (_selectedDriver) {
            setTimeout(tick, frameTime);
        }
    }

    tick();
}

function _updateSpeedGauge(speed) {
    var maxSpeed = 370;
    var pct = Math.min(speed / maxSpeed, 1);

    // The arc path length is ~251.2 (pi * 80)
    var arcLength = 251.2;
    var fillLength = pct * arcLength;

    var gaugeFill = document.getElementById('gauge-fill');
    if (gaugeFill) {
        gaugeFill.setAttribute('stroke-dasharray', fillLength + ' ' + arcLength);

        // Color: green -> yellow -> red
        var hue = 120 - pct * 120; // 120=green, 0=red
        gaugeFill.setAttribute('stroke', 'hsl(' + hue + ', 80%, 50%)');
    }

    _setTextContent('gauge-speed', Math.round(speed));
}


// ══════════════════════════════════════════════════════════════
// CIRCUIT MAP — Canvas Rendering (preserved from original)
// ══════════════════════════════════════════════════════════════

function initCircuitMap(canvasId, trackPoints, driverMap, sessionKey) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || !trackPoints || trackPoints.length === 0) return;

    var ctx = canvas.getContext('2d');
    var container = canvas.parentElement;

    // Zoom and Pan state
    var zoom = 1.0;
    var panX = 0;
    var panY = 0;
    var isDragging = false;
    var startDragX = 0;
    var startDragY = 0;

    // Track state
    var transformedTrack = [];
    var carPositions = {};
    var prevCarPositions = {};
    var animStartTime = 0;
    var animDuration = 2000; // ms to interpolate

    // Transform parameters cache
    var cachedTransform = null;

    // Inject zoom/pan controls overlay dynamically
    (function createControlsOverlay() {
        var existing = container.querySelector('.map-controls-overlay');
        if (existing) existing.remove();

        var overlay = document.createElement('div');
        overlay.className = 'map-controls-overlay';
        overlay.innerHTML = '<button class="map-ctrl-btn" id="map-zoom-in" title="Zoom In">＋</button>' +
            '<button class="map-ctrl-btn" id="map-zoom-out" title="Zoom Out">－</button>' +
            '<button class="map-ctrl-btn" id="map-recenter" title="Recenter Track">↺</button>';
        container.appendChild(overlay);

        document.getElementById('map-zoom-in').addEventListener('click', function() {
            adjustZoom(1.3);
        });
        document.getElementById('map-zoom-out').addEventListener('click', function() {
            adjustZoom(1 / 1.3);
        });
        document.getElementById('map-recenter').addEventListener('click', function() {
            resetZoom();
        });
    })();

    // Resize canvas to container
    function resizeCanvas() {
        var rect = container.getBoundingClientRect();
        canvas.width = rect.width;
        canvas.height = rect.height;
        transformTrack();
        render();
    }

    // Transform raw coordinates to base canvas space
    function transformTrack() {
        if (trackPoints.length === 0) return;

        var minX = Infinity, maxX = -Infinity;
        var minY = Infinity, maxY = -Infinity;

        for (var i = 0; i < trackPoints.length; i++) {
            var p = trackPoints[i];
            if (p.x < minX) minX = p.x;
            if (p.x > maxX) maxX = p.x;
            if (p.y < minY) minY = p.y;
            if (p.y > maxY) maxY = p.y;
        }

        var rangeX = maxX - minX || 1;
        var rangeY = maxY - minY || 1;
        var padding = 60;
        var drawW = canvas.width - padding * 2;
        var drawH = canvas.height - padding * 2;
        var scale = Math.min(drawW / rangeX, drawH / rangeY);
        var offsetX = padding + (drawW - rangeX * scale) / 2;
        var offsetY = padding + (drawH - rangeY * scale) / 2;

        cachedTransform = {
            minX: minX, minY: minY,
            scale: scale,
            offsetX: offsetX, offsetY: offsetY,
        };

        transformedTrack = [];
        for (var j = 0; j < trackPoints.length; j++) {
            transformedTrack.push({
                x: offsetX + (trackPoints[j].x - minX) * scale,
                y: offsetY + (trackPoints[j].y - minY) * scale,
            });
        }
    }

    // Transform coordinate relative to current Zoom and Pan
    function getTransformedPoint(x, y) {
        var cx = canvas.width / 2;
        var cy = canvas.height / 2;
        return {
            x: cx + panX + (x - cx) * zoom,
            y: cy + panY + (y - cy) * zoom
        };
    }

    // Draw track path
    function drawTrackPath(gapThreshold, startEnd, strokeStyle, lineWidth) {
        ctx.beginPath();
        var firstPt = getTransformedPoint(transformedTrack[0].x, transformedTrack[0].y);
        ctx.moveTo(firstPt.x, firstPt.y);

        for (var i = 1; i < transformedTrack.length; i++) {
            var dx = transformedTrack[i].x - transformedTrack[i - 1].x;
            var dy = transformedTrack[i].y - transformedTrack[i - 1].y;
            var dist = Math.sqrt(dx * dx + dy * dy);
            var pt = getTransformedPoint(transformedTrack[i].x, transformedTrack[i].y);

            if (dist > gapThreshold) {
                ctx.moveTo(pt.x, pt.y);
            } else {
                ctx.lineTo(pt.x, pt.y);
            }
        }

        if (startEnd < gapThreshold) {
            ctx.lineTo(firstPt.x, firstPt.y);
        }

        ctx.strokeStyle = strokeStyle;
        ctx.lineWidth = lineWidth * Math.max(0.8, Math.min(zoom, 2.5));
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.stroke();
    }

    // Draw Start/Finish Line Checkerboard
    function drawStartFinishLine() {
        if (transformedTrack.length < 2) return;

        var pt = getTransformedPoint(transformedTrack[0].x, transformedTrack[0].y);
        var nextPt = getTransformedPoint(transformedTrack[1].x, transformedTrack[1].y);

        var dx = nextPt.x - pt.x;
        var dy = nextPt.y - pt.y;
        var len = Math.sqrt(dx * dx + dy * dy) || 1;
        var px = -dy / len;
        var py = dx / len;

        var w = 12 * Math.max(0.8, Math.min(zoom, 2.5));

        ctx.beginPath();
        ctx.moveTo(pt.x - px * w, pt.y - py * w);
        ctx.lineTo(pt.x + px * w, pt.y + py * w);
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 4 * Math.max(0.8, Math.min(zoom, 2.5));
        ctx.stroke();

        ctx.beginPath();
        ctx.moveTo(pt.x - px * w, pt.y - py * w);
        ctx.lineTo(pt.x + px * w, pt.y + py * w);
        ctx.strokeStyle = '#000000';
        ctx.setLineDash([3, 3]);
        ctx.stroke();
        ctx.setLineDash([]);

        ctx.font = "bold 9px 'IBM Plex Mono', monospace";
        ctx.fillStyle = 'rgba(255, 255, 255, 0.7)';
        ctx.textAlign = 'left';
        ctx.fillText("START/FINISH", pt.x + px * (w + 6), pt.y + py * (w + 6) + 3);
    }

    // Render the track and car positions
    function render() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        if (transformedTrack.length > 1) {
            var totalDist = 0;
            var distCount = 0;
            for (var d = 1; d < transformedTrack.length; d++) {
                var ddx = transformedTrack[d].x - transformedTrack[d-1].x;
                var ddy = transformedTrack[d].y - transformedTrack[d-1].y;
                totalDist += Math.sqrt(ddx*ddx + ddy*ddy);
                distCount++;
            }
            var avgDist = distCount > 0 ? totalDist / distCount : 10;
            var gapThreshold = avgDist * 6;

            var startEnd = Math.sqrt(
                Math.pow(transformedTrack[0].x - transformedTrack[transformedTrack.length-1].x, 2) +
                Math.pow(transformedTrack[0].y - transformedTrack[transformedTrack.length-1].y, 2)
            );

            drawTrackPath(gapThreshold, startEnd, 'rgba(255, 255, 255, 0.15)', 8);
            drawTrackPath(gapThreshold, startEnd, 'rgba(255, 255, 255, 0.08)', 4);
            drawStartFinishLine();
        }

        // Draw car positions
        var now = performance.now();
        var t = Math.min(1, (now - animStartTime) / animDuration);
        t = easeOutCubic(t);

        var driverNums = Object.keys(carPositions);
        for (var d = 0; d < driverNums.length; d++) {
            var dn = driverNums[d];
            var pos = carPositions[dn];
            var prev = prevCarPositions[dn] || pos;

            var cx = prev.x + (pos.x - prev.x) * t;
            var cy = prev.y + (pos.y - prev.y) * t;
            var pt = getTransformedPoint(cx, cy);

            var driver = driverMap[parseInt(dn)] || {};
            var colour = driver.team_colour ? '#' + driver.team_colour : '#888';
            var acronym = driver.name_acronym || dn;
            var isSelected = _selectedDriver === parseInt(dn);

            // Glow for selected driver
            if (isSelected) {
                ctx.beginPath();
                ctx.arc(pt.x, pt.y, 18, 0, Math.PI * 2);
                ctx.fillStyle = colour + '44';
                ctx.fill();

                ctx.beginPath();
                ctx.arc(pt.x, pt.y, 14, 0, Math.PI * 2);
                ctx.strokeStyle = colour;
                ctx.lineWidth = 2;
                ctx.stroke();
            }

            ctx.beginPath();
            ctx.arc(pt.x, pt.y, isSelected ? 8 : 5, 0, Math.PI * 2);
            ctx.fillStyle = colour;
            ctx.fill();
            ctx.strokeStyle = '#0a0e17';
            ctx.lineWidth = 1.5;
            ctx.stroke();

            ctx.font = (isSelected ? "bold 11px" : "bold 9px") + " 'IBM Plex Mono', monospace";
            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.shadowColor = 'rgba(0, 0, 0, 0.8)';
            ctx.shadowBlur = 4;
            ctx.fillText(acronym, pt.x, pt.y - (isSelected ? 14 : 10));
            ctx.shadowBlur = 0;
        }

        if (t < 1) {
            requestAnimationFrame(render);
        }
    }

    function easeOutCubic(x) {
        return 1 - Math.pow(1 - x, 3);
    }

    // Convert coordinates to canvas base space
    function locationToCanvas(rawX, rawY) {
        if (!cachedTransform) return { x: 0, y: 0 };
        return {
            x: cachedTransform.offsetX + (rawX - cachedTransform.minX) * cachedTransform.scale,
            y: cachedTransform.offsetY + (rawY - cachedTransform.minY) * cachedTransform.scale,
        };
    }

    // Update car positions from fetched location data — exposed globally for WebSocket
    window.updateCarPositions = function(locations) {
        prevCarPositions = JSON.parse(JSON.stringify(carPositions));
        animStartTime = performance.now();

        for (var i = 0; i < locations.length; i++) {
            var loc = locations[i];
            var canvasPos = locationToCanvas(loc.x, loc.y);
            carPositions[loc.driver_number] = canvasPos;
        }

        requestAnimationFrame(render);
    };

    // Interactive Drag & Zoom Controls
    function adjustZoom(factor) {
        zoom = Math.max(0.5, Math.min(zoom * factor, 8.0));
        render();
    }

    function resetZoom() {
        zoom = 1.0;
        panX = 0;
        panY = 0;
        render();
    }

    // Mouse Panning & Zooming Event Listeners
    canvas.style.cursor = 'grab';
    canvas.addEventListener('mousedown', function(e) {
        isDragging = true;
        startDragX = e.clientX - panX;
        startDragY = e.clientY - panY;
        canvas.style.cursor = 'grabbing';
    });

    canvas.addEventListener('mousemove', function(e) {
        if (isDragging) {
            panX = e.clientX - startDragX;
            panY = e.clientY - startDragY;
            render();
        }
    });

    window.addEventListener('mouseup', function() {
        if (isDragging) {
            isDragging = false;
            canvas.style.cursor = 'grab';
        }
    });

    canvas.addEventListener('wheel', function(e) {
        e.preventDefault();
        var factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        adjustZoom(factor);
    }, { passive: false });

    // Touch Support for Mobile
    canvas.addEventListener('touchstart', function(e) {
        if (e.touches.length === 1) {
            isDragging = true;
            startDragX = e.touches[0].clientX - panX;
            startDragY = e.touches[0].clientY - panY;
        }
    });

    canvas.addEventListener('touchmove', function(e) {
        if (isDragging && e.touches.length === 1) {
            panX = e.touches[0].clientX - startDragX;
            panY = e.touches[0].clientY - startDragY;
            render();
        }
    });

    canvas.addEventListener('touchend', function() {
        isDragging = false;
    });

    // Initial setup
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);
}
