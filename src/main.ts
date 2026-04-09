import { getLocations, saveLocation, removeLocation, fetchWeatherData, fetchHistoricalWeatherData } from './api';
import { blendForecasts, groupData } from './data';
import {
    showLoading,
    showError,
    showContent,
    renderHeader,
    renderDaySummaries,
    renderDayDetail,
    renderLocationSwitcher
} from './render';
import { initSnowEngine } from './utils';
import { DayData } from './types';

let currentLocation = 'palisades';
let currentModelMode = 'best_match';
let currentSlrMode = 'kinematic';
let currentDaysData: DayData[] = [];
let allSkiAreas: any[] = [];

// ── In-memory cache ───────────────────────────────────────────────────────────
// Keyed by "locationKey|modelMode" so only location/model changes trigger a fetch.
interface WeatherCache {
    hrrrData: any;
    ecmwfData: any;
    location: any;
}
const weatherCache = new Map<string, WeatherCache>();

async function loadSkiAreas() {
    try {
        const baseUrl = import.meta.env.DEV ? '' : '/mysnow';
        const response = await fetch(`${baseUrl}/ski-areas.json`);
        allSkiAreas = await response.json();
    } catch (err) {
        console.error("Failed to load ski areas data:", err);
        allSkiAreas = [];
    }
}

async function loadForecast() {
    const cacheKey = `${currentLocation}|${currentModelMode}`;
    const isCacheHit = weatherCache.has(cacheKey);

    // Only show the spinner when we actually need to hit the network
    if (!isCacheHit) showLoading();

    try {
        let cached = weatherCache.get(cacheKey);

        if (!cached) {
            // First time for this location+model — hit the network
            const result = await fetchWeatherData(currentLocation, currentModelMode);
            cached = { hrrrData: result.hrrrData, ecmwfData: result.ecmwfData, location: result.location };
            weatherCache.set(cacheKey, cached);
        }

        const { hrrrData, ecmwfData, location } = cached;

        const blendedData = blendForecasts(hrrrData, ecmwfData, location, currentSlrMode);
        currentDaysData = groupData(blendedData).slice(0, 14);

        const currentData = currentDaysData.length > 0 && currentDaysData[0].hourly.length > 0
            ? currentDaysData[0].hourly[0]
            : null;

        renderHeader(location, currentData);
        renderDaySummaries(currentDaysData, (day) => {
            renderDayDetail(day);
        });

        const modelLegend = document.querySelector('.model-legend');
        if (modelLegend) {
            if (currentModelMode === 'best_match') {
                modelLegend.innerHTML = `<span class="badge model-mixed">BEST MATCH</span> Automatically selects the best available local models.`;
            } else {
                modelLegend.innerHTML = `
                    <span class="badge model-hrrr">HRRR</span> 0-48 hours (3km resolution)
                    <br>
                    <span class="badge model-ecmwf">ECMWF</span> 3-16 days (9km resolution)
                `;
            }
        }

        showContent();
    } catch (err) {
        console.error(err);
        showError('Failed to load forecast data. Please try again later.');
    }
}

async function loadHistoricalForecast(startDate: string, model: string) {
    // End date is 5 days after start date for a good backtest window
    const startObj = new Date(startDate);
    const endObj = new Date(startObj.getTime() + 5 * 24 * 60 * 60 * 1000);
    const endDate = endObj.toISOString().split('T')[0];

    showLoading();

    try {
        const result = await fetchHistoricalWeatherData(currentLocation, startDate, endDate, model);
        const { hrrrData, ecmwfData, location } = result;

        const blendedData = blendForecasts(hrrrData, ecmwfData, location, currentSlrMode);
        currentDaysData = groupData(blendedData);

        const currentData = currentDaysData.length > 0 && currentDaysData[0].hourly.length > 0
            ? currentDaysData[0].hourly[0]
            : null;

        renderHeader(location, currentData);
        renderDaySummaries(currentDaysData, (day) => {
            renderDayDetail(day);
        });

        const modelLegend = document.querySelector('.model-legend');
        if (modelLegend) {
            const modelName = model === 'best_match' ? 'Best Match' : model === 'gfs_hrrr' ? 'HRRR' : 'ECMWF IFS';
            modelLegend.innerHTML = `
                <span class="badge model-best">HISTORICAL ARCHIVE (${modelName})</span> 
                Showing archived high-resolution forecasts from <b>${startDate}</b> to <b>${endDate}</b>.
            `;
        }

        const resetBtn = document.getElementById('hist-reset-btn');
        if (resetBtn) resetBtn.classList.remove('hidden');

        showContent();
    } catch (err) {
        console.error(err);
        showError('Failed to load historical data. Ensure the date is between 2022 and yesterday.');
    }
}

function updateSwitcher() {
    renderLocationSwitcher(
        getLocations(),
        currentLocation,
        (id) => {
            currentLocation = id;
            updateSwitcher();

            const mode = (document.querySelector('input[name="app-mode"]:checked') as HTMLInputElement)?.value;
            if (mode === 'history') {
                const startDate = (document.getElementById('hist-start') as HTMLInputElement)?.value;
                const model = (document.getElementById('hist-model') as HTMLSelectElement)?.value;
                if (startDate && model) {
                    loadHistoricalForecast(startDate, model);
                }
            } else {
                loadForecast();
            }
        },
        (id) => {
            if (currentLocation === id) currentLocation = 'palisades';
            removeLocation(id);
            updateSwitcher();
            if (currentLocation === 'palisades') loadForecast();
        }
    );
}

function initLocListeners() {
    updateSwitcher();

    const searchInput = document.getElementById('ski-area-search') as HTMLInputElement;
    const searchResults = document.getElementById('search-results') as HTMLElement;
    const coordsInput = document.getElementById('loc-coords') as HTMLInputElement;
    const addBtn = document.getElementById('add-location-btn');

    if (searchInput) {
        searchInput.addEventListener('input', (e: Event) => {
            const target = e.target as HTMLInputElement;
            const query = target.value.toLowerCase().trim();

            if (query.length < 2) {
                searchResults.innerHTML = '';
                searchResults.classList.add('hidden');
                return;
            }

            const matches = allSkiAreas
                .filter(area => area.name.toLowerCase().includes(query))
                .slice(0, 10);

            if (matches.length > 0) {
                searchResults.innerHTML = matches.map(area => `
                    <div class="search-item" data-name="${area.name}" data-lat="${area.lat}" data-lon="${area.lon}">
                        <span class="search-item-name">${area.name}</span>
                        <span class="search-item-meta">${area.region ? area.region + ', ' : ''}${area.country}</span>
                    </div>
                `).join('');
                searchResults.classList.remove('hidden');
            } else {
                searchResults.innerHTML = '<div class="search-item">No matches found</div>';
                searchResults.classList.remove('hidden');
            }
        });
    }

    if (searchResults) {
        searchResults.addEventListener('click', (e: Event) => {
            const target = e.target as HTMLElement;
            const item = target.closest('.search-item') as HTMLElement | null;
            if (!item || !item.dataset.lat) return;

            const { name, lat, lon } = item.dataset;

            if (searchInput) searchInput.value = name || '';
            if (coordsInput) coordsInput.value = `${lat}, ${lon}`;

            searchResults.innerHTML = '';
            searchResults.classList.add('hidden');
        });
    }

    if (addBtn) {
        addBtn.addEventListener('click', () => {
            const name = searchInput?.value.trim() || '';
            const coordsRaw = coordsInput?.value.trim() || '';

            let lat, lon;
            const coords = coordsRaw.split(/[\s,]+/);
            if (coords.length >= 2 && !isNaN(parseFloat(coords[0])) && !isNaN(parseFloat(coords[1]))) {
                lat = coords[0];
                lon = coords[1];
            }

            if (lat && lon) {
                const displayName = name || `${parseFloat(lat).toFixed(2)}, ${parseFloat(lon).toFixed(2)}`;
                const id = 'loc-' + Date.now();
                saveLocation(id, displayName, lat, lon);
                currentLocation = id;

                if (searchInput) searchInput.value = '';
                if (coordsInput) coordsInput.value = '';

                updateSwitcher();
                loadForecast();
            } else {
                alert('Please provide valid coordinates (lat, lon).');
            }
        });
    }

    document.addEventListener('click', (e: MouseEvent) => {
        if (searchInput && !searchInput.contains(e.target as Node) && searchResults && !searchResults.contains(e.target as Node)) {
            searchResults.classList.add('hidden');
        }
    });

    const modelToggle = document.getElementById('model-toggle') as HTMLInputElement;
    const toggleTexts = document.querySelectorAll('.toggle-text');

    if (modelToggle) {
        modelToggle.addEventListener('change', () => {
            currentModelMode = modelToggle.checked ? 'hrrr_ecmwf' : 'best_match';
            toggleTexts.forEach(txt => {
                const el = txt as HTMLElement;
                if (el.dataset.mode === currentModelMode) {
                    el.classList.add('active');
                } else {
                    el.classList.remove('active');
                }
            });
            loadForecast();
        });
    }

    const slrGroup = document.getElementById('slr-algorithm-group');
    if (slrGroup) {
        slrGroup.addEventListener('change', (e: Event) => {
            const target = e.target as HTMLInputElement;
            if (target.name === 'slr-alg') {
                currentSlrMode = target.value;
                const startDate = (document.getElementById('hist-start') as HTMLInputElement)?.value;
                const model = (document.getElementById('hist-model') as HTMLSelectElement)?.value;
                const isHistorical = !(document.getElementById('hist-reset-btn')?.classList.contains('hidden'));
                if (isHistorical && startDate && model) {
                    loadHistoricalForecast(startDate, model);
                } else {
                    loadForecast();
                }
            }
        });
    }

    const histBtn = document.getElementById('hist-backtest-btn');
    const histResetBtn = document.getElementById('hist-reset-btn');
    const histDateInput = document.getElementById('hist-start') as HTMLInputElement;
    const histModelSelect = document.getElementById('hist-model') as HTMLSelectElement;

    if (histBtn) {
        histBtn.addEventListener('click', () => {
            const startDate = histDateInput?.value;
            const model = histModelSelect?.value;
            if (startDate && model) {
                loadHistoricalForecast(startDate, model);
            } else {
                alert('Please select a start date and model for the backtest.');
            }
        });
    }

    const appModeRadios = document.querySelectorAll('input[name="app-mode"]');
    const forecastControls = document.getElementById('forecast-controls');
    const historyControls = document.getElementById('history-controls');

    appModeRadios.forEach(radio => {
        radio.addEventListener('change', (e: Event) => {
            const mode = (e.target as HTMLInputElement).value;
            if (mode === 'history') {
                document.body.classList.add('mode-history');
                forecastControls?.classList.add('hidden');
                historyControls?.classList.remove('hidden');

                // If a date is already picked, load it, otherwise show a default or stay loading
                const startDate = histDateInput?.value;
                const model = histModelSelect?.value;
                if (startDate && model) {
                    loadHistoricalForecast(startDate, model);
                }
            } else {
                document.body.classList.remove('mode-history');
                forecastControls?.classList.remove('hidden');
                historyControls?.classList.add('hidden');
                loadForecast();
            }
        });
    });
}

async function init() {
    initSnowEngine();
    await loadSkiAreas();
    initLocListeners();
    const retryBtn = document.getElementById('retry-btn');
    if (retryBtn) retryBtn.addEventListener('click', loadForecast);
    loadForecast();
    registerServiceWorker();
}

// ── PWA Service Worker Registration ──────────────────────────────────────────
function registerServiceWorker() {
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker
            .register('./sw.js', { scope: '/mysnow/' })
            .then((reg) => console.log('[SW] Registered:', reg.scope))
            .catch((err) => console.warn('[SW] Registration failed:', err));
    }
}

document.addEventListener('DOMContentLoaded', init);

