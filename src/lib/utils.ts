export function isToday(date: Date): boolean {
    const today = new Date();
    return date.getDate() === today.getDate() &&
        date.getMonth() === today.getMonth() &&
        date.getFullYear() === today.getFullYear();
}

export function isTomorrow(date: Date): boolean {
    const tomorrow = new Date();
    tomorrow.setDate(tomorrow.getDate() + 1);
    return date.getDate() === tomorrow.getDate() &&
        date.getMonth() === tomorrow.getMonth() &&
        date.getFullYear() === tomorrow.getFullYear();
}

export function formatHour(timeStr: string): string {
    return timeStr.split('T')[1];
}

export function formatTemp(temp: number | null): string {
    return temp !== null ? temp.toFixed(1) + '°C' : '--';
}

// Simple snow particle effect
export function initSnowEngine() {
    const container = document.querySelector('.snow-particles');
    if (!container) return;
    
    container.innerHTML = '';
    const particleCount = 20;
    
    for (let i = 0; i < particleCount; i++) {
        const particle = document.createElement('div');
        
        // Randomize size, opacity, and positioning
        const size = Math.random() * 4 + 2;
        const opacity = Math.random() * 0.5 + 0.1;
        const left = Math.random() * 100;
        const duration = Math.random() * 3 + 2;
        const delay = Math.random() * 2;
        
        particle.style.cssText = `
            position: absolute;
            background: #fff;
            border-radius: 50%;
            width: ${size}px;
            height: ${size}px;
            opacity: ${opacity};
            left: ${left}%;
            top: -10px;
            pointer-events: none;
            animation: fall ${duration}s linear ${delay}s infinite;
        `;
        
        container.appendChild(particle);
    }
    
    // Add keyframes definition if not present
    if (!document.getElementById('snow-keyframes')) {
        const style = document.createElement('style');
        style.id = 'snow-keyframes';
        style.textContent = `
            @keyframes fall {
                0% { transform: translateY(-10px) translateX(0); }
                100% { transform: translateY(200px) translateX(20px); }
            }
        `;
        document.head.appendChild(style);
    }
}

/** Map a snow-to-liquid ratio to an HSL color for UI highlights. */
export function getSlrColor(slr: number): string {
    const mapped = Math.min(Math.max(slr, 5), 15);
    const hue = mapped <= 10
        ? 180 + ((mapped - 5) / 5) * 30      // cyan → blue
        : (210 + ((mapped - 10) / 5) * 180) % 360; // blue → orange
    return `hsl(${hue}, 100%, 60%)`;
}

/** Map a WMO weather interpretation code to a human-readable label and emoji icon. */
export function getWeatherDescription(code: number | null): { label: string; icon: string } {
    const placeholder = { label: '--', icon: '—' };
    if (code === null) return placeholder;
    
    const descriptions: { [key: number]: { label: string; icon: string } } = {
        0: { label: 'Clear', icon: '☀️' },
        1: { label: 'Mainly Clear', icon: '🌤️' },
        2: { label: 'Partly Cloudy', icon: '⛅' },
        3: { label: 'Overcast', icon: '☁️' },
        45: { label: 'Fog', icon: '🌫️' },
        48: { label: 'Rime Fog', icon: '🌫️' },
        51: { label: 'Light Drizzle', icon: '🌧️' },
        53: { label: 'Drizzle', icon: '🌧️' },
        55: { label: 'Dense Drizzle', icon: '🌧️' },
        56: { label: 'Light Freezing Drizzle', icon: '❄️🌧️' },
        57: { label: 'Dense Freezing Drizzle', icon: '❄️🌧️' },
        61: { label: 'Light Rain', icon: '🌦️' },
        63: { label: 'Moderate Rain', icon: '🌧️' },
        65: { label: 'Heavy Rain', icon: '🌧️' },
        66: { label: 'Light Freezing Rain', icon: '❄️🌧️' },
        67: { label: 'Heavy Freezing Rain', icon: '❄️🌧️' },
        71: { label: 'Light Snow', icon: '🌨️' },
        73: { label: 'Moderate Snow', icon: '❄️' },
        75: { label: 'Heavy Snow', icon: '❄️❄️' },
        77: { label: 'Snow Grains', icon: '❄️' },
        80: { label: 'Light Rain Showers', icon: '🌦️' },
        81: { label: 'Moderate Rain Showers', icon: '🌧️' },
        82: { label: 'Violent Rain Showers', icon: '🌧️' },
        85: { label: 'Light Snow Showers', icon: '🌨️' },
        86: { label: 'Heavy Snow Showers', icon: '❄️' },
        95: { label: 'Thunderstorm', icon: '⛈️' },
        96: { label: 'Thunderstorm with Hail', icon: '⛈️' },
        99: { label: 'Thunderstorm with Heavy Hail', icon: '⛈️' }
    };
    
    return descriptions[code] || { label: 'Unknown', icon: '—' };
}
