import { isToday, isTomorrow } from './utils.js';

const els = {
    locationInfo: document.getElementById('location-info'),
    currentConditions: document.getElementById('current-conditions'),
    daySummaryContainer: document.getElementById('day-summary-container'),
    dayDetailContainer: document.getElementById('day-detail-container'),
    loadingState: document.getElementById('loading-state'),
    errorState: document.getElementById('error-state'),
    errorMessage: document.querySelector('.error-message'),
    forecastContent: document.getElementById('forecast-content'),
    locationSwitcher: document.getElementById('location-switcher')
};

export function renderLocationSwitcher(locations, currentLocationId, onSelect, onRemove) {
    els.locationSwitcher.innerHTML = '';

    Object.values(locations).forEach(loc => {
        const btn = document.createElement('button');
        btn.className = `location-btn ${loc.id === currentLocationId ? 'active' : ''}`;
        btn.textContent = loc.name;

        btn.addEventListener('click', (e) => {
            if (e.target.classList.contains('remove-loc')) return;
            onSelect(loc.id);
        });

        if (loc.isCustom) {
            const removeSpan = document.createElement('span');
            removeSpan.className = 'remove-loc';
            removeSpan.textContent = '×';
            removeSpan.title = 'Remove Location';
            removeSpan.addEventListener('click', (e) => {
                e.stopPropagation();
                onRemove(loc.id);
            });
            btn.appendChild(removeSpan);
        }

        els.locationSwitcher.appendChild(btn);
    });
}

export function showLoading() {
    els.loadingState.classList.remove('hidden');
    els.errorState.classList.add('hidden');
    els.forecastContent.classList.add('hidden');
}

export function showError(msg) {
    els.loadingState.classList.add('hidden');
    els.errorState.classList.remove('hidden');
    els.errorMessage.textContent = msg;
    els.forecastContent.classList.add('hidden');
}

export function showContent() {
    els.loadingState.classList.add('hidden');
    els.errorState.classList.add('hidden');
    els.forecastContent.classList.remove('hidden');
}

export function renderHeader(location, currentData) {
    els.locationInfo.innerHTML = `
        ${location.latitude}°N, ${Math.abs(location.longitude)}°W | Elev: ${location.elevationFt.toLocaleString()}ft (${location.elevationM.toLocaleString()}m)
        <br>
        <span style="opacity: 0.7; font-size: 0.75rem;">Last updated: ${new Date().toLocaleTimeString()}</span>
    `;

    if (!currentData) {
        els.currentConditions.innerHTML = '';
        return;
    }

    const { temperature, snowfall, slr, windSpeed, windDir, snowDepth } = currentData;

    let slrBadge = '';
    if (slr) {
        const type = slr < 10 ? 'Wet' : (slr > 15 ? 'Powder' : 'Standard');
        slrBadge = `<span class="badge slr-badge slr-${type.toLowerCase()}">${slr.toFixed(1)}:1 ${type}</span>`;
    }

    const windStr = windSpeed !== null ? `${windSpeed.toFixed(0)} km/h <span class="wind-arrow" style="transform: rotate(${windDir}deg)">↓</span>` : '--';

    els.currentConditions.innerHTML = `
        <div class="condition-item">
            <span class="condition-label">Temp</span>
            <span class="condition-value">${temperature.toFixed(1)}°C</span>
        </div>
        <div class="condition-item">
            <span class="condition-label">Snow Rate</span>
            <span class="condition-value">${snowfall.toFixed(1)} cm/hr</span>
            ${slrBadge}
        </div>
        <div class="condition-item">
            <span class="condition-label">Wind</span>
            <span class="condition-value chart-wind">${windStr}</span>
        </div>
    `;
}

function getDayTitle(dateObj) {
    if (isToday(dateObj)) return "Today";
    if (isTomorrow(dateObj)) return "Tomorrow";
    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    return `${days[dateObj.getDay()]} ${dateObj.getDate()}`;
}

export function renderDaySummaries(daysData, onSelectDay) {
    els.daySummaryContainer.innerHTML = '';

    daysData.forEach((day, index) => {
        const card = document.createElement('div');
        const isBigStorm = day.totalSnowfall >= 20;

        // Calculate average SLR for the day to determine highlight color
        let stormHue = 210; // Default Blue
        if (isBigStorm && day.totalPrecipitation > 0) {
            const avgSlr = (day.totalSnowfall * 10) / day.totalPrecipitation;
            const mappedSlr = Math.min(Math.max(avgSlr, 5), 20);
            if (mappedSlr <= 10) {
                stormHue = 180 + ((mappedSlr - 5) / 5) * 30;
            } else {
                stormHue = (210 + ((mappedSlr - 10) / 10) * 180) % 360;
            }
        }

        card.className = `day-card ${index === 0 ? 'selected' : ''} ${isBigStorm ? 'big-storm' : ''}`;
        if (isBigStorm) {
            card.style.setProperty('--storm-color', `hsl(${stormHue}, 100%, 60%)`);
            // Set RGB version for pulse shadow transparency
            card.style.setProperty('--storm-color-rgb', stormHue === 210 ? '56, 189, 248' : ''); 
            // Note: If we want dynamic RGB based on Hue, we would need a converter, 
            // but for blue (210) we use the accent-blue. 
            // For others, let's just use the blue as fallback or calculate it.
            if (stormHue !== 210) {
                // Simple HSL to RGB approximation for the pulse effect
                const s = 1;
                const l = 0.6;
                const c = (1 - Math.abs(2 * l - 1)) * s;
                const x = c * (1 - Math.abs((stormHue / 60) % 2 - 1));
                const m = l - c / 2;
                let r, g, b;
                if (stormHue < 60) { r = c; g = x; b = 0; }
                else if (stormHue < 120) { r = x; g = c; b = 0; }
                else if (stormHue < 180) { r = 0; g = c; b = x; }
                else if (stormHue < 240) { r = 0; g = x; b = c; }
                else if (stormHue < 300) { r = x; g = 0; b = c; }
                else { r = c; g = 0; b = x; }
                card.style.setProperty('--storm-color-rgb', `${Math.round((r + m) * 255)}, ${Math.round((g + m) * 255)}, ${Math.round((b + m) * 255)}`);
            }
        }

        let modelClass = day.modelString === 'HRRR' ? 'model-hrrr' :
            (day.modelString === 'ECMWF' ? 'model-ecmwf' :
                (day.modelString === 'BEST' ? 'model-best' : 'model-mixed'));
        let modelLabel = day.modelString === 'BEST' ? 'Best Match' : day.modelString;

        card.innerHTML = `
            <div class="card-date">${getDayTitle(day.dateObj)}</div>
            <div class="card-snow">
                <span class="snow-value">${day.totalSnowfall.toFixed(1)}</span>
                <span class="snow-unit">cm</span>
            </div>
            <div class="badge-container">
                <span class="badge ${modelClass}">${modelLabel}</span>
                ${isBigStorm ? '<span class="badge storm-badge">STORM</span>' : ''}
            </div>
        `;

        card.addEventListener('click', () => {
            document.querySelectorAll('.day-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            onSelectDay(day);
        });

        els.daySummaryContainer.appendChild(card);
    });

    if (daysData.length > 0) {
        onSelectDay(daysData[0]);
    }
}

export function renderDayDetail(day) {
    els.dayDetailContainer.innerHTML = '';
    els.dayDetailContainer.classList.remove('hidden');

    const maxBarHeight = 150; // px
    const scaleMax = 10.0; // cm of snowfall (max scale)

    let maxTemp = -Infinity; let minTemp = Infinity;
    day.hourly.forEach(h => {
        if (h.temperature !== null) {
            if (h.temperature > maxTemp) maxTemp = h.temperature;
            if (h.temperature < minTemp) minTemp = h.temperature;
        }
    });

    if (minTemp === Infinity) { minTemp = 0; maxTemp = 0; }

    const targetRange = Math.max(maxTemp - minTemp, 5);
    const midTemp = (maxTemp + minTemp) / 2;
    const tempPadding = targetRange * 0.15;
    const chartMin = midTemp - (targetRange / 2) - tempPadding;
    const chartMax = midTemp + (targetRange / 2) + tempPadding;
    const chartRange = chartMax - chartMin;

    const colWidth = 60;
    const colGap = 8;
    const totalColWidth = colWidth + colGap;
    const svgWidth = 24 * totalColWidth;

    const boxLeft = 9 * totalColWidth;
    const boxWidth = 8 * totalColWidth - colGap;

    const skiBoxHtml = `
        <div class="ski-line-marker" style="left: ${boxLeft}px;"></div>
        <div class="ski-line-marker" style="left: ${boxLeft + boxWidth + colGap}px;"></div>
    `;

    let snowCols = '';
    let tempCols = '';
    let metricCols = '';

    let tempStops = '';
    let tempPath = `M`;

    day.hourly.forEach((h, index) => {
        // Use snowfall for bar height, fall back to precipitation for rain
        const barVal = (h.snowfall > 0) ? h.snowfall : (h.precipitation || 0);
        const height = (Math.min(barVal, scaleMax) / scaleMax) * maxBarHeight;
        const hrLabel = h.time.split('T')[1];

        let astroIcon = '';
        if (day.sunrise && day.sunrise.substring(0, 13) === h.time.substring(0, 13)) astroIcon = ' 🌅';
        if (day.sunset && day.sunset.substring(0, 13) === h.time.substring(0, 13)) astroIcon = ' 🌇';

        let slrText = '--';
        let barColor = 'rgba(255, 255, 255, 0.1)';

        if (h.slrCategory === 'rain') {
            slrText = 'Rain';
            barColor = 'hsl(120, 70%, 50%)'; // Green for rain
        } else if (h.slr !== null) {
            slrText = h.slr.toFixed(1) + ':1';
            let mappedSlr = Math.min(Math.max(h.slr, 5), 20);
            let hue;
            if (mappedSlr <= 10) {
                // Light Blue (180) to Blue (210)
                hue = 180 + ((mappedSlr - 5) / 5) * 30;
            } else {
                // Blue (210) to Orange (390/30) via Purple
                hue = (210 + ((mappedSlr - 10) / 10) * 180) % 360;
            }
            barColor = `hsl(${hue}, 100%, 60%)`;
        }

        let slrBadge = h.slr !== null ? `<div class="badge slr-badge chart-slr" style="margin-bottom:2px;">${slrText}</div>` : '';

        let windStr = `<div class="chart-wind" style="font-size: 0.8rem; font-weight: 600; height: 1.2rem; display: flex; align-items: center; justify-content: center; width: 100%;">--</div>`;
        if (h.windSpeed !== null) {
            windStr = `<div class="chart-wind" style="font-size: 0.8rem; font-weight: 600; height: 1.2rem; display: flex; align-items: center; justify-content: center; width: 100%;">${h.windSpeed.toFixed(0)} km/h <span class="wind-arrow" style="transform: rotate(${h.windDir}deg); font-weight: bold; margin-left: 2px;">↓</span></div>`;
        }

        const colCenter = (index * totalColWidth) + (colWidth / 2);
        let tempY = h.temperature !== null ? 90 - ((h.temperature - chartMin) / chartRange) * 80 : 90;

        tempPath += `${index === 0 ? '' : ' L'}${colCenter},${tempY}`;

        let tColor = h.temperature > 0 ? '#f43f5e' : '#38bdf8';
        tempStops += `<stop offset="${(index / 23) * 100}%" stop-color="${tColor}" />`;

        snowCols += `
            <div class="chart-col-group" style="z-index: 1;">
                <div class="chart-time label-top">${hrLabel}${astroIcon}</div>
                ${slrBadge}
                <div class="chart-bar-wrapper">
                    <div class="chart-bar" style="height: ${height}px; background: ${barColor}; opacity: ${h.precipitation > 0 ? 1 : 0}"></div>
                </div>
                <div class="chart-snow-bottom" style="font-size: 0.70rem; font-weight: bold; margin-top: 4px; color: var(--snow-white); white-space: nowrap;">
                    ${h.snowfall > 0.05 ? h.snowfall.toFixed(1) + ' cm' : (h.precipitation > 0 ? h.precipitation.toFixed(1) + ' mm' : '--')}
                </div>
            </div>`;

        tempCols += `
            <div class="chart-col-group" style="z-index: 1;">
                <div class="chart-time label-top">${hrLabel}${astroIcon}</div>
                <div class="chart-metrics" style="margin-top: 125px;">
                    <div class="metric-row" style="border: none;">
                        <span class="metric-val ${h.temperature > 0 ? 'val-hot' : 'val-cold'}" style="font-size: 1rem; font-weight: 700;">${h.temperature !== null ? h.temperature.toFixed(1) + '°C' : '--'}</span>
                    </div>
                </div>
            </div>`;

        metricCols += `
            <div class="chart-col-group" style="z-index: 1;">
                <div class="chart-time label-top">${hrLabel}${astroIcon}</div>
                <div class="chart-metrics" style="margin-top: 10px; width: 100%;">
                    ${windStr}
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">Precip</span><span class="metric-val" style="color:var(--snow-white);">${h.precipitation > 0 ? h.precipitation.toFixed(1) + ' mm' : '--'}</span></div>
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">SLR</span><span class="metric-val" style="color: ${barColor}; font-weight:bold;">${slrText}</span></div>
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">Feels</span><span class="metric-val">${h.feelsLike !== null ? h.feelsLike.toFixed(1) + '°C' : '--'}</span></div>
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">Snow Lvl</span><span class="metric-val">${h.snowLevel !== null ? h.snowLevel.toFixed(0) + 'm' : '--'}</span></div>
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">Precip %</span><span class="metric-val">${h.precipChance !== null ? h.precipChance + '%' : '--'}</span></div>
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">RH</span><span class="metric-val">${h.rh !== null ? h.rh.toFixed(0) + '%' : '--'}</span></div>
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">Cloud</span><span class="metric-val">${h.clouds !== null ? h.clouds.toFixed(0) + '%' : '--'}</span></div>
                </div>
            </div>`;
    });

    const snowHtml = `
        <div class="snow-chart-scroll chart-wrapper-relative" id="snow-chart-scroll">
            ${skiBoxHtml}
            ${snowCols}
        </div>
    `;

    const tempHtml = `
        <div class="temp-chart-scroll chart-wrapper-relative" id="temp-chart-scroll">
            ${skiBoxHtml}
            <svg width="${svgWidth}" height="100" style="position:absolute; top:35px; left:0; pointer-events:none; z-index:10; overflow:visible;">
                <defs>
                    <linearGradient id="temp-grad" x1="0%" y1="0%" x2="100%" y2="0%">
                        ${tempStops}
                    </linearGradient>
                </defs>
                <path fill="none" stroke="url(#temp-grad)" stroke-width="4" stroke-linejoin="round" d="${tempPath}" filter="drop-shadow(0 2px 4px rgba(0,0,0,0.5))" />
            </svg>
            ${tempCols}
        </div>
    `;

    const metricsHtml = `
        <div class="metrics-chart-scroll chart-wrapper-relative" id="metrics-chart-scroll">
            ${skiBoxHtml}
            ${metricCols}
        </div>
    `;

    const slrLegendHtml = `
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 1rem; font-size: 0.8rem; justify-content: center;">
            <div style="font-weight: 600;">Precip Type:</div>
            <div style="display: flex; align-items: center; gap: 4px; margin-right: 15px;">
                <div style="width: 12px; height: 12px; background: hsl(120, 70%, 50%); border-radius: 2px;"></div> Rain
            </div>
            <div style="font-weight: 600;">SLR Quality:</div>
            <div>Wet (5:1)</div>
            <div style="width: 150px; height: 12px; background: linear-gradient(to right, hsl(180, 100%, 60%), hsl(210, 100%, 60%), hsl(300, 100%, 60%), hsl(30, 100%, 60%)); border-radius: 6px;"></div>
            <div>Dry (>20:1)</div>
        </div>
    `;

    els.dayDetailContainer.innerHTML = `
        <div class="detail-header" style="margin-bottom: 1rem;">
            <h3 class="detail-date">${getDayTitle(day.dateObj)}</h3>
            <div class="detail-total">
                ${day.totalSnowfall.toFixed(1)} cm total
                <div style="font-size: 0.75rem; color: var(--text-secondary); text-align: right; margin-top: 4px;">Est. Snow Depth: ${day.snowDepth}</div>
            </div>
        </div>
        ${slrLegendHtml}
        <h4 style="margin-bottom: 0.5rem; font-size:1rem;">Snowfall Intensity (Scaled up to 10cm/hr)</h4>
        ${snowHtml}
        <h4 style="margin-bottom: 0.5rem; font-size:1rem;">Temperature Trend</h4>
        ${tempHtml}
        <h4 style="margin-bottom: 0.5rem; font-size:1rem;">Weather Telemetry</h4>
        ${metricsHtml}
    `;

    setTimeout(() => {
        const scrollers = [
            document.getElementById('snow-chart-scroll'),
            document.getElementById('temp-chart-scroll'),
            document.getElementById('metrics-chart-scroll')
        ];

        let activeScroller = null;
        scrollers.forEach(scroller => {
            if (!scroller) return;
            scroller.addEventListener('scroll', function () {
                if (activeScroller !== null && activeScroller !== this) return;
                activeScroller = this;
                scrollers.forEach(s => {
                    if (s && s !== this) s.scrollLeft = this.scrollLeft;
                });
                clearTimeout(this.resetSync);
                this.resetSync = setTimeout(() => { activeScroller = null; }, 50);
            });
        });

        if (scrollers[0]) {
            const centerPos = (12 * totalColWidth) - (scrollers[0].offsetWidth / 2) + (colWidth / 2);
            scrollers.forEach(s => { if (s) s.scrollLeft = Math.max(0, centerPos); });
        }
    }, 10);
}
