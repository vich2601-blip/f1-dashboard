/* ═══════════════════════════════════════════════════════════
   F1 Dashboard — Client-side Interactivity
   ═══════════════════════════════════════════════════════════ */

// ── Mobile Navigation ──────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
    const navToggle = document.getElementById('nav-toggle');
    const navLinks = document.getElementById('nav-links');

    if (navToggle && navLinks) {
        navToggle.addEventListener('click', function () {
            navLinks.classList.toggle('open');
        });

        // Close on link click
        navLinks.querySelectorAll('.nav-link').forEach(function (link) {
            link.addEventListener('click', function () {
                navLinks.classList.remove('open');
            });
        });
    }
});

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
    Chart.defaults.font.family = "'Titillium Web', 'Inter', sans-serif";
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
                    bodyFont: { family: "'JetBrains Mono', monospace" },
                },
            },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: { font: { family: "'JetBrains Mono', monospace", size: 11 } },
                },
                y: {
                    grid: { display: false },
                    ticks: { font: { family: "'JetBrains Mono', monospace", size: 12, weight: '600' } },
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
        var parts = timeStr.split(':');
        if (parts.length === 2) {
            return parseFloat(parts[0]) * 60 + parseFloat(parts[1]);
        }
        return parseFloat(timeStr);
    }

    var datasets = [];
    var maxLap = 0;

    Object.keys(lapData).forEach(function (driverCode) {
        var driver = lapData[driverCode];
        var lapTimes = driver.laps.map(function (lap) {
            if (lap.lap > maxLap) maxLap = lap.lap;
            return {
                x: lap.lap,
                y: timeToSeconds(lap.time),
            };
        }).filter(function (d) { return d.y !== null && d.y > 0; });

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
                        font: { family: "'JetBrains Mono', monospace", size: 11 },
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
                    ticks: { font: { family: "'JetBrains Mono', monospace", size: 10 } },
                },
                y: {
                    title: { display: true, text: 'Lap Time (s)', font: { weight: '600' } },
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: {
                        font: { family: "'JetBrains Mono', monospace", size: 10 },
                        callback: function (value) {
                            var mins = Math.floor(value / 60);
                            var secs = (value % 60).toFixed(0);
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
                        font: { family: "'JetBrains Mono', monospace", size: 11 },
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
                    ticks: { font: { family: "'JetBrains Mono', monospace", size: 10 } },
                },
                y: {
                    title: { display: true, text: 'Position', font: { weight: '600' } },
                    reverse: true,
                    min: 1,
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: {
                        font: { family: "'JetBrains Mono', monospace", size: 10 },
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
                        font: { family: "'JetBrains Mono', monospace", size: 12 },
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
                    ticks: { font: { family: "'JetBrains Mono', monospace", size: 11, weight: '600' } },
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: { font: { family: "'JetBrains Mono', monospace", size: 11 } },
                },
            },
            animation: { duration: 1200, easing: 'easeOutQuart' },
        },
    });
}

// ══════════════════════════════════════════════════════════════
// LIVE TIMING — Circuit Map & Session Picker
// ══════════════════════════════════════════════════════════════

// ── Session Picker Navigation ──────────────────────────────
function changeYear(year) {
    window.location.href = '/live?year=' + year;
}

function changeSession(sessionKey) {
    var yearSelect = document.getElementById('year-select');
    var year = yearSelect ? yearSelect.value : 2026;
    window.location.href = '/live?year=' + year + '&session=' + sessionKey;
}

// ── Circuit Map ────────────────────────────────────────────
function initCircuitMap(canvasId, trackPoints, driverMap, sessionKey) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || !trackPoints || trackPoints.length === 0) return;

    var ctx = canvas.getContext('2d');
    var container = canvas.parentElement;

    // Track state
    var transformedTrack = [];
    var carPositions = {};
    var prevCarPositions = {};
    var animStartTime = 0;
    var animDuration = 2000; // ms to interpolate

    // Fix #2: Cache transform parameters to avoid recomputation in locationToCanvas
    var cachedTransform = null;

    // Poll failure tracking (Fix #13)
    var consecutiveFailures = 0;
    var MAX_FAILURES_BEFORE_WARNING = 3;

    // Resize canvas to container
    function resizeCanvas() {
        var rect = container.getBoundingClientRect();
        canvas.width = rect.width;
        canvas.height = rect.height;
        transformTrack();
        render();
    }

    // Transform raw x,y coordinates to canvas space and cache transform params
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
        var padding = 40;
        var drawW = canvas.width - padding * 2;
        var drawH = canvas.height - padding * 2;
        var scale = Math.min(drawW / rangeX, drawH / rangeY);
        var offsetX = padding + (drawW - rangeX * scale) / 2;
        var offsetY = padding + (drawH - rangeY * scale) / 2;

        // Cache the transform parameters for locationToCanvas
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

    // Fix #5: Extract track path drawing into a reusable helper
    function drawTrackPath(gapThreshold, startEnd, strokeStyle, lineWidth) {
        ctx.beginPath();
        ctx.moveTo(transformedTrack[0].x, transformedTrack[0].y);
        for (var i = 1; i < transformedTrack.length; i++) {
            var dx = transformedTrack[i].x - transformedTrack[i - 1].x;
            var dy = transformedTrack[i].y - transformedTrack[i - 1].y;
            var dist = Math.sqrt(dx * dx + dy * dy);
            if (dist > gapThreshold) {
                ctx.moveTo(transformedTrack[i].x, transformedTrack[i].y);
            } else {
                ctx.lineTo(transformedTrack[i].x, transformedTrack[i].y);
            }
        }
        // Close the loop if start and end are close
        if (startEnd < gapThreshold) {
            ctx.lineTo(transformedTrack[0].x, transformedTrack[0].y);
        }
        ctx.strokeStyle = strokeStyle;
        ctx.lineWidth = lineWidth;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.stroke();
    }

    // Render the track and car positions
    function render() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Draw track outline with gap detection
        if (transformedTrack.length > 1) {
            // Calculate gap threshold based on average inter-point distance
            var totalDist = 0;
            var distCount = 0;
            for (var d = 1; d < transformedTrack.length; d++) {
                var ddx = transformedTrack[d].x - transformedTrack[d-1].x;
                var ddy = transformedTrack[d].y - transformedTrack[d-1].y;
                totalDist += Math.sqrt(ddx*ddx + ddy*ddy);
                distCount++;
            }
            var avgDist = distCount > 0 ? totalDist / distCount : 10;
            var gapThreshold = avgDist * 6; // 6x average = gap (pit lane, etc.)

            var startEnd = Math.sqrt(
                Math.pow(transformedTrack[0].x - transformedTrack[transformedTrack.length-1].x, 2) +
                Math.pow(transformedTrack[0].y - transformedTrack[transformedTrack.length-1].y, 2)
            );

            // Outer glow track + inner bright line using shared helper
            drawTrackPath(gapThreshold, startEnd, 'rgba(255, 255, 255, 0.12)', 8);
            drawTrackPath(gapThreshold, startEnd, 'rgba(255, 255, 255, 0.06)', 4);
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

            // Interpolate position
            var cx = prev.x + (pos.x - prev.x) * t;
            var cy = prev.y + (pos.y - prev.y) * t;

            // Get driver info
            var driver = driverMap[parseInt(dn)] || {};
            var colour = driver.team_colour ? '#' + driver.team_colour : '#888';
            var acronym = driver.name_acronym || dn;

            // Glow
            ctx.beginPath();
            ctx.arc(cx, cy, 10, 0, Math.PI * 2);
            ctx.fillStyle = colour + '33';
            ctx.fill();

            // Dot
            ctx.beginPath();
            ctx.arc(cx, cy, 5, 0, Math.PI * 2);
            ctx.fillStyle = colour;
            ctx.fill();
            ctx.strokeStyle = 'rgba(0, 0, 0, 0.5)';
            ctx.lineWidth = 1;
            ctx.stroke();

            // Label
            ctx.font = "bold 8px 'JetBrains Mono', monospace";
            ctx.fillStyle = '#fff';
            ctx.textAlign = 'center';
            ctx.fillText(acronym, cx, cy - 10);
        }

        if (t < 1) {
            requestAnimationFrame(render);
        }
    }

    function easeOutCubic(x) {
        return 1 - Math.pow(1 - x, 3);
    }

    // Fix #2: Use cached transform parameters instead of recomputing
    function locationToCanvas(rawX, rawY) {
        if (!cachedTransform) return { x: 0, y: 0 };

        return {
            x: cachedTransform.offsetX + (rawX - cachedTransform.minX) * cachedTransform.scale,
            y: cachedTransform.offsetY + (rawY - cachedTransform.minY) * cachedTransform.scale,
        };
    }

    // Update car positions from fetched location data
    function updateCarPositions(locations) {
        prevCarPositions = JSON.parse(JSON.stringify(carPositions));
        animStartTime = performance.now();

        for (var i = 0; i < locations.length; i++) {
            var loc = locations[i];
            var canvasPos = locationToCanvas(loc.x, loc.y);
            carPositions[loc.driver_number] = canvasPos;
        }

        requestAnimationFrame(render);
    }

    // Fix #13: Show/hide disconnection indicator
    function showDisconnected(show) {
        var existing = document.getElementById('poll-disconnected');
        if (show && !existing) {
            var indicator = document.createElement('div');
            indicator.id = 'poll-disconnected';
            indicator.className = 'poll-disconnected';
            indicator.innerHTML = '⚠️ <span>Connection lost — retrying…</span>';
            var panel = document.getElementById('circuit-map-panel');
            if (panel) {
                var header = panel.querySelector('.panel-header');
                if (header) header.appendChild(indicator);
            }
        } else if (!show && existing) {
            existing.remove();
        }
    }

    // Initial setup
    resizeCanvas();
    window.addEventListener('resize', resizeCanvas);

    // Start polling for live car positions
    var pollCount = 0;
    var pollIntervalId = null;

    function pollLiveData() {
        // Every 6th poll (~60s), do a full refresh; otherwise just fast data
        var isFull = (pollCount % 6 === 0);
        var url = '/api/live/' + sessionKey + (isFull ? '?full=1' : '');
        pollCount++;

        fetch(url)
            .then(function(resp) {
                if (resp.ok) return resp.json();
                return resp.json()
                    .catch(function() { return {}; })
                    .then(function(data) {
                        throw new Error(data.error || 'Live timing request failed (' + resp.status + ')');
                    });
            })
            .then(function(data) {
                // Reset failure counter on success
                consecutiveFailures = 0;
                showDisconnected(false);

                if (data.locations && data.locations.length > 0) {
                    updateCarPositions(data.locations);
                }
            })
            .catch(function(err) {
                consecutiveFailures++;
                console.log('[Live Poll Error]', err, '(failures: ' + consecutiveFailures + ')');
                if (consecutiveFailures >= MAX_FAILURES_BEFORE_WARNING) {
                    showDisconnected(true);
                }
            });
    }

    // Initial poll + interval (10s to respect API rate limits)
    pollLiveData();
    pollIntervalId = setInterval(pollLiveData, 10000);

    // Clean up polling when navigating away
    window.addEventListener('beforeunload', function() {
        if (pollIntervalId) clearInterval(pollIntervalId);
    });
}
