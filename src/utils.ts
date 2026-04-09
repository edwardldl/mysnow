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
