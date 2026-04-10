import { getSLRCategory } from './data';
import { isToday, isTomorrow, initSnowEngine, formatHour, formatTemp, getWeatherDescription } from './utils';
import { Location, BlendedHour, DayData } from './types';

/**
 * Map a snow-to-liquid ratio to an HSL hue.
 * SLR 5 (wet) → cyan (180°), SLR 10 (standard) → blue (210°), SLR 15+ (powder) → orange (30°).
 */
function slrToHsl(slr: number): string {
    const mapped = Math.min(Math.max(slr, 5), 15);
    const hue = mapped <= 10
        ? 180 + ((mapped - 5) / 5) * 30      // cyan → blue
        : (210 + ((mapped - 10) / 5) * 180) % 360; // blue → orange
    return `hsl(${hue}, 100%, 60%)`;
}

const els = {
    locationInfo: document.getElementById('location-info') as HTMLElement,
    currentConditions: document.getElementById('current-conditions') as HTMLElement,
    daySummaryContainer: document.getElementById('day-summary-container') as HTMLElement,
    dayDetailContainer: document.getElementById('day-detail-container') as HTMLElement,
    loadingState: document.getElementById('loading-state') as HTMLElement,
    errorState: document.getElementById('error-state') as HTMLElement,
    errorMessage: document.querySelector('.error-message') as HTMLElement,
    forecastContent: document.getElementById('forecast-content') as HTMLElement,
    locationSwitcher: document.getElementById('location-switcher') as HTMLElement
};

export function renderLocationSwitcher(locations: any, currentLocationId: string, onSelect: (id: string) => void, onRemove: (id: string) => void) {
    els.locationSwitcher.innerHTML = '';

    Object.values(locations).forEach((loc: any) => {
        const btn = document.createElement('button');
        btn.className = `location-btn ${loc.id === currentLocationId ? 'active' : ''}`;
        btn.textContent = loc.name;

        btn.addEventListener('click', (e: Event) => {
            const target = e.target as HTMLElement;
            if (target.classList.contains('remove-loc')) return;
            onSelect(loc.id);
        });

        if (loc.isCustom) {
            const removeSpan = document.createElement('span');
            removeSpan.className = 'remove-loc';
            removeSpan.textContent = '×';
            removeSpan.title = 'Remove Location';
            removeSpan.addEventListener('click', (e: Event) => {
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

export function showError(msg: string) {
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

export function renderHeader(location: Location, currentData: BlendedHour | null) {
    els.locationInfo.innerHTML = `
        ${location.latitude}°N, ${Math.abs(location.longitude)}°W | Elev: ${location.elevationFt.toLocaleString()}ft (${location.elevationM.toLocaleString()}m)
        <br>
        <span style="opacity: 0.7; font-size: 0.75rem;">Last updated: ${new Date().toLocaleTimeString()}</span>
    `;

    if (!currentData) {
        els.currentConditions.innerHTML = '';
        return;
    }

    const { temperature, snowfall, slr, windSpeed, windDir, weatherCode } = currentData;
    const weather = getWeatherDescription(weatherCode);

    let slrBadge = '';
    if (slr) {
        const type = slr < 10 ? 'Wet' : (slr >= 15 ? 'Powder' : 'Standard');
        slrBadge = `<span class="badge slr-badge slr-${type.toLowerCase()}" style="margin-top: 4px;">${slr.toFixed(1)}:1 ${type}</span>`;
    }

    const windStr = windSpeed !== null ? `${windSpeed.toFixed(0)} km/h <span class="wind-arrow" style="transform: rotate(${windDir}deg)">↓</span>` : '--';

    els.currentConditions.innerHTML = `
        <div class="condition-item weather-main">
            <span class="condition-icon">${weather.icon}</span>
            <div class="condition-details">
                <span class="condition-label">Current</span>
                <span class="condition-value">${weather.label}</span>
            </div>
        </div>
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
        <div style="width: 100%; margin-top: 0.5rem; font-size: 0.7rem; color: var(--text-secondary);">
            <a href="https://open-meteo.com/" target="_blank" rel="noopener" style="color: var(--text-secondary); text-decoration: underline;">Weather by Open-Meteo.com</a>
        </div>
    `;
}

function getDayTitle(dateObj: Date) {
    if (isToday(dateObj)) return "Today";
    if (isTomorrow(dateObj)) return "Tomorrow";
    const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    return `${days[dateObj.getDay()]} ${dateObj.getDate()}`;
}

export function renderDaySummaries(daysData: DayData[], onSelectDay: (day: DayData) => void) {
    els.daySummaryContainer.innerHTML = '';

    daysData.forEach((day, index) => {
        const card = document.createElement('div');
        const isBigStorm = day.totalSnowfall >= 15;
        const avgSlr = day.totalPrecipitation > 0 ? (day.totalSnowfall * 10) / day.totalPrecipitation : 0;

        const dayColor = slrToHsl(avgSlr);
        // Derive CSS-variable RGB string from the HSL colour for box-shadow / overlay use
        const mapped = Math.min(Math.max(avgSlr, 5), 15);
        const hue = mapped <= 10
            ? 180 + ((mapped - 5) / 5) * 30
            : (210 + ((mapped - 10) / 5) * 180) % 360;
        const s = 1, l = 0.6;
        const c = (1 - Math.abs(2 * l - 1)) * s;
        const x = c * (1 - Math.abs((hue / 60) % 2 - 1));
        const m = l - c / 2;
        let r, g, b;
        if (hue < 60) { r = c; g = x; b = 0; }
        else if (hue < 120) { r = x; g = c; b = 0; }
        else if (hue < 180) { r = 0; g = c; b = x; }
        else if (hue < 240) { r = 0; g = x; b = c; }
        else if (hue < 300) { r = x; g = 0; b = c; }
        else { r = c; g = 0; b = x; }
        const rgbStr = `${Math.round((r + m) * 255)}, ${Math.round((g + m) * 255)}, ${Math.round((b + m) * 255)}`;

        card.className = `day-card ${index === 0 ? 'selected' : ''} ${isBigStorm ? 'big-storm' : ''} ${avgSlr >= 15 ? 'powder-day' : ''}`;
        card.style.setProperty('--day-color', dayColor);
        card.style.setProperty('--day-color-rgb', rgbStr);

        if (isBigStorm) {
            card.style.setProperty('--storm-color', dayColor);
            card.style.setProperty('--storm-color-rgb', rgbStr);
        }

        let modelClass = 'model-mixed';
        let modelLabel = day.modelString;

        if (day.modelString === 'HRRR') modelClass = 'model-hrrr';
        else if (day.modelString === 'ECMWF') modelClass = 'model-ecmwf';
        else if (day.modelString === 'BEST') {
            modelClass = 'model-best';
            modelLabel = 'Best Match';
        } else if (day.modelString === 'GFS') modelClass = 'model-gfs';
        else if (day.modelString.includes('GEM')) modelClass = 'model-gem';
        else if (day.modelString === 'NBM') modelClass = 'model-nbm';
        else if (day.modelString === 'NAM') modelClass = 'model-nam';
        else if (day.modelString === 'HRRR_ECMWF') {
            modelClass = 'model-mixed';
            modelLabel = 'Blended';
        }

        card.innerHTML = `
            <div class="card-date">${getDayTitle(day.dateObj)}</div>
            <div class="card-snow">
                <span class="snow-value">${day.totalSnowfall.toFixed(1)}</span>
                <span class="snow-unit">cm</span>
            </div>
            <div class="badge-container">
                <span class="badge ${modelClass}">${modelLabel}</span>
                ${isBigStorm ? '<span class="badge storm-badge">STORM</span>' : ''}
                ${avgSlr >= 15 ? '<span class="badge powder-badge">POWDER</span>' : ''}
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

export function renderDayDetail(day: DayData) {
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

    let snowCols = '';
    let tempCols = '';
    let metricCols = '';

    let tempStops = '';
    let tempPath = `M`;

    day.hourly.forEach((h, index) => {
        const barVal = (h.snowfall > 0) ? h.snowfall : (h.precipitation || 0);
        const height = (Math.min(barVal, scaleMax) / scaleMax) * maxBarHeight;
        const hrLabel = formatHour(h.time);

        let astroIcon = '';
        if (day.sunrise && day.sunrise.substring(0, 13) === h.time.substring(0, 13)) astroIcon = ' 🌅';
        if (day.sunset && day.sunset.substring(0, 13) === h.time.substring(0, 13)) astroIcon = ' 🌇';

        let slrText = '--';
        let barColor = 'rgba(255, 255, 255, 0.1)';

        if (h.slrCategory === 'rain') {
            slrText = 'Rain';
            barColor = 'hsl(120, 70%, 50%)';
        } else if (h.slr !== null) {
            slrText = h.slr.toFixed(1) + ':1';
            barColor = slrToHsl(h.slr);
        }

        const slrBadge = h.slr !== null ? `<div class="badge slr-badge chart-slr" style="margin-bottom:2px;">${slrText}</div>` : '';

        let windStr = `<div class="chart-wind" style="font-size: 0.8rem; font-weight: 600; height: 1.2rem; display: flex; align-items: center; justify-content: center; width: 100%;">--</div>`;
        if (h.windSpeed !== null && h.windDir !== null) {
            windStr = `<div class="chart-wind" style="font-size: 0.8rem; font-weight: 600; height: 1.2rem; display: flex; align-items: center; justify-content: center; width: 100%;">${h.windSpeed.toFixed(0)} km/h <span class="wind-arrow" style="transform: rotate(${h.windDir}deg); font-weight: bold; margin-left: 2px;">↓</span></div>`;
        }

        const hour = parseInt(hrLabel.split(':')[0]);
        let hourStyle = '';
        if (hour >= 9 && hour <= 16) {
            const progress = hour <= 12 ? (hour - 9) / 3.5 : 1 - ((hour - 12.5) / 3.5);
            const hue = 45 - (progress * 15);
            hourStyle = ` style="color: hsl(${hue}, 100%, 50%);"`;
        }

        snowCols += `
            <div class="chart-col-group" style="z-index: 1;">
                <div class="chart-time label-top"${hourStyle}>${hrLabel}${astroIcon}</div>
                ${h.slr !== null ? `<div class="slr-label-group-top" style="color: ${barColor}">${h.slr.toFixed(0)}:1</div>` : '<div class="slr-label-group-top">&nbsp;</div>'}
                <div class="chart-bar-wrapper">
                    <div class="chart-bar" style="height: ${height}px; background: ${barColor}; opacity: ${h.precipitation > 0 ? 1 : 0}"></div>
                </div>
                <div class="chart-snow-bottom" style="font-size: 0.70rem; font-weight: bold; margin-top: 4px; color: var(--snow-white); white-space: nowrap;">
                    ${h.snowfall > 0.05 ? h.snowfall.toFixed(1) + ' cm' : (h.precipitation > 0 ? h.precipitation.toFixed(1) + ' mm' : '--')}
                </div>
            </div>`;

        // Temperature Bar Logic
        const tempHeight = h.temperature !== null ? ((h.temperature - chartMin) / chartRange) * 100 : 0;
        
        // Calculate dynamic hue: -20C (240 deg Blue) to +20C (0 deg Red)
        const clampedTemp = Math.max(-20, Math.min(20, h.temperature ?? 0));
        const tempHue = 240 - ((clampedTemp + 20) / 40) * 240;
        const tempColor = `hsl(${tempHue}, 80%, 60%)`;
        const tempGradient = `linear-gradient(to top, hsl(${tempHue}, 80%, 40%), hsl(${tempHue}, 80%, 70%))`;

        tempCols += `
            <div class="chart-col-group" style="z-index: 1;">
                <div class="chart-time label-top"${hourStyle}>${hrLabel}${astroIcon}</div>
                <div class="chart-bar-wrapper" style="height: 100px;">
                    <div class="chart-bar" style="height: ${tempHeight}px; background: ${tempGradient}; border-radius: 4px;"></div>
                </div>
                <div class="chart-metrics" style="margin-top: 10px;">
                    <div class="metric-row" style="border: none;">
                        <span class="metric-val" style="font-size: 1rem; font-weight: 700; color: ${tempColor};">${formatTemp(h.temperature)}</span>
                    </div>
                </div>
            </div>`;

        metricCols += `
            <div class="chart-col-group" style="z-index: 1;">
                <div class="chart-time label-top"${hourStyle}>${hrLabel}${astroIcon}</div>
                <div class="chart-metrics" style="margin-top: 10px; width: 100%;">
                    ${windStr}
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">Precip</span><span class="metric-val" style="color:var(--snow-white);">${h.precipitation > 0 ? h.precipitation.toFixed(1) + ' mm' : '--'}</span></div>
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">SLR</span><span class="metric-val" style="color: ${barColor}; font-weight:bold;">${slrText}</span></div>
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">Feels</span><span class="metric-val">${formatTemp(h.feelsLike)}</span></div>
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">Snow Lvl</span><span class="metric-val">${(h.snowLevel != null && !isNaN(h.snowLevel)) ? h.snowLevel.toFixed(0) + 'm' : '--'}</span></div>
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">Precip %</span><span class="metric-val">${h.precipChance !== null ? h.precipChance + '%' : '--'}</span></div>
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">RH</span><span class="metric-val">${h.rh !== null ? h.rh.toFixed(0) + '%' : '--'}</span></div>
                    <div class="metric-row" style="width: 100%;"><span class="metric-label">Cloud</span><span class="metric-val">${h.clouds !== null ? h.clouds.toFixed(0) + '%' : '--'}</span></div>
                </div>
            </div>`;
    });

    const snowHtml = `
        <div class="snow-chart-scroll chart-wrapper-relative" id="snow-chart-scroll">
            ${snowCols}
        </div>
    `;

    const tempHtml = `
        <div class="temp-chart-scroll chart-wrapper-relative" id="temp-chart-scroll">
            ${tempCols}
        </div>
    `;

    const metricsHtml = `
        <div class="metrics-chart-scroll chart-wrapper-relative" id="metrics-chart-scroll">
            ${metricCols}
        </div>
    `;

    const snowLegend = `
        <div style="display: flex; align-items: center; gap: 15px; font-size: 0.8rem;">
            <div style="display: flex; align-items: center; gap: 8px;">
                <div style="width: 10px; height: 10px; background: hsl(120, 70%, 50%); border-radius: 2px;"></div>
                <span style="color: var(--text-secondary);">Rain</span>
            </div>
            <div style="display: flex; align-items: center; gap: 8px;">
                <span style="color: var(--text-secondary);">Wet</span>
                <div style="width: 100px; height: 8px; background: linear-gradient(to right, hsl(180, 100%, 60%), hsl(210, 100%, 60%), hsl(300, 100%, 60%), hsl(30, 100%, 60%)); border-radius: 4px;"></div>
                <span style="color: var(--text-secondary);">Powder</span>
            </div>
        </div>
    `;

    const tempLegend = `
        <div style="display: flex; align-items: center; gap: 8px; font-size: 0.8rem;">
            <span style="color: var(--text-secondary);">-20°C</span>
            <div style="width: 120px; height: 8px; background: linear-gradient(to right, hsl(240, 100%, 60%), hsl(120, 100%, 60%), hsl(0, 100%, 60%)); border-radius: 4px;"></div>
            <span style="color: var(--text-secondary);">+20°C</span>
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
        <div class="chart-section-header" style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem;">
            <h4 style="margin: 0; font-size:1rem;">Snowfall Intensity</h4>
            ${snowLegend}
        </div>
        ${snowHtml}
        
        <div class="chart-section-header" style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.5rem; margin-top: 1.5rem;">
            <h4 style="margin: 0; font-size:1rem;">Temperature Trend</h4>
            ${tempLegend}
        </div>
        ${tempHtml}
        
        <h4 style="margin-bottom: 0.5rem; margin-top: 1.5rem; font-size:1rem;">Weather Telemetry</h4>
        ${metricsHtml}
    `;

    setTimeout(() => {
        const scrollers = [
            document.getElementById('snow-chart-scroll') as HTMLElement,
            document.getElementById('temp-chart-scroll') as HTMLElement,
            document.getElementById('metrics-chart-scroll') as HTMLElement
        ];

        let activeScroller: HTMLElement | null = null;
        scrollers.forEach((scroller: HTMLElement | null) => {
            if (!scroller) return;
            scroller.addEventListener('scroll', function (this: HTMLElement) {
                if (activeScroller !== null && activeScroller !== this) return;
                activeScroller = this;
                scrollers.forEach(s => {
                    if (s && s !== this) s.scrollLeft = this.scrollLeft;
                });
                const self = this as any;
                clearTimeout(self.resetSync);
                self.resetSync = setTimeout(() => { activeScroller = null; }, 50);
            });
        });

        if (scrollers[0]) {
            const centerPos = (12 * totalColWidth) - (scrollers[0].offsetWidth / 2) + (colWidth / 2);
            scrollers.forEach(s => { if (s) s.scrollLeft = Math.max(0, centerPos); });
        }
    }, 10);
}
