import { getLocations, saveLocation, removeLocation, fetchWeatherData } from './api';
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

async function loadSkiAreas() {
    try {
        const response = await fetch('./ski_areas.csv');
        const csvText = await response.text();
        const lines = csvText.split('\n');
        const headers = lines[0].split(',');

        const nameIdx = headers.indexOf('name');
        const countryIdx = headers.indexOf('countries');
        const regionIdx = headers.indexOf('regions');
        const latIdx = headers.indexOf('lat');
        const lonIdx = headers.indexOf('lng');

        const parseCSVLine = (line: string) => {
            const result: string[] = [];
            let cell = '';
            let inQuotes = false;
            for (let i = 0; i < line.length; i++) {
                const char = line[i];
                if (char === '"') {
                    inQuotes = !inQuotes;
                } else if (char === ',' && !inQuotes) {
                    result.push(cell.trim());
                    cell = '';
                } else {
                    cell += char;
                }
            }
            result.push(cell.trim());
            return result;
        };

        allSkiAreas = lines.slice(1).map(line => {
            if (!line.trim()) return null;
            const cells = parseCSVLine(line);
            if (cells.length < headers.length) return null;

            return {
                name: cells[nameIdx].replace(/^"|"$/g, ''),
                country: cells[countryIdx].replace(/^"|"$/g, ''),
                region: cells[regionIdx].replace(/^"|"$/g, ''),
                lat: cells[latIdx],
                lon: cells[lonIdx]
            };
        }).filter(area => area && area.name && area.lat && area.lon);
    } catch (err) {
        console.error("Failed to load ski areas CSV:", err);
    }
}

async function loadForecast() {
    showLoading();
    try {
        const { hrrrData, ecmwfData, location } = await fetchWeatherData(currentLocation, currentModelMode);

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

function updateSwitcher() {
    renderLocationSwitcher(
        getLocations(),
        currentLocation,
        (id) => {
            currentLocation = id;
            updateSwitcher();
            loadForecast();
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
                loadForecast();
            }
        });
    }
}

function init() {
    initSnowEngine();
    loadSkiAreas();
    initLocListeners();
    const retryBtn = document.getElementById('retry-btn');
    if (retryBtn) retryBtn.addEventListener('click', loadForecast);
    loadForecast();
}

document.addEventListener('DOMContentLoaded', init);
