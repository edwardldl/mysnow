"use client";

import React, { useRef } from "react";
import { Snowflake, Thermometer, Info, Wind, Navigation2, Sun } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils_tailwind";
import { DayData } from "@/lib/types";
import { getSlrColor } from "@/lib/utils";

interface ForecastDashboardProps {
  days: DayData[];
  isLoading: boolean;
  selectedDate: string | null;
}

export default function ForecastDashboard({ days, isLoading, selectedDate }: ForecastDashboardProps) {
  const scrollRefs = useRef<(HTMLDivElement | null)[]>([]);
  const [primaryContainer, setPrimaryContainer] = React.useState<HTMLDivElement | null>(null);

  // Synchronize scrolling across multiple chart containers
  const handleScroll = (e: React.UIEvent<HTMLDivElement>, index: number) => {
    const scrollLeft = (e.target as HTMLDivElement).scrollLeft;
    scrollRefs.current.forEach((ref, i) => {
      if (ref && i !== index) {
        ref.scrollLeft = scrollLeft;
      }
    });
  };

  const [currentHourISO, setCurrentHourISO] = React.useState<string | null>(null);
  const [todayStr, setTodayStr] = React.useState<string | null>(null);

  React.useEffect(() => {
    const now = new Date();
    // Robust local date/hour detection for America/Los_Angeles
    const today = now.toLocaleDateString('en-CA', { timeZone: 'America/Los_Angeles' });
    const fmt = new Intl.DateTimeFormat('en-US', {
      hour: '2-digit',
      hourCycle: 'h23',
      timeZone: 'America/Los_Angeles'
    });
    const localHour = fmt.format(now);

    setTodayStr(today);
    setCurrentHourISO(`${today}T${localHour}`);
  }, []);

  const nowRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const performScroll = (behavior: ScrollBehavior = 'smooth') => {
      const target = nowRef.current;
      if (primaryContainer && target) {
        const targetRect = target.getBoundingClientRect();
        const containerRect = primaryContainer.getBoundingClientRect();
        const relativeLeft = targetRect.left - containerRect.left + primaryContainer.scrollLeft;
        const targetX = relativeLeft - primaryContainer.offsetWidth / 2 + target.offsetWidth / 2;

        scrollRefs.current.forEach(c => {
          if (c) c.scrollTo({ left: targetX, behavior });
        });
      }
    };

    if (nowRef.current && primaryContainer) {
      // Immediate jump with a short delay for layout
      const t0 = setTimeout(() => performScroll('auto'), 50);
      const t1 = setTimeout(() => performScroll('smooth'), 500);
      const t2 = setTimeout(() => performScroll('smooth'), 1500);

      return () => {
        clearTimeout(t0);
        clearTimeout(t1);
        clearTimeout(t2);
      };
    }
  }, [selectedDate, currentHourISO, days, primaryContainer]);

  if (isLoading) {
    return (
      <div className="max-w-7xl mx-auto px-4 md:px-8 mt-12 flex flex-col gap-6">
        <div className="w-full h-96 glass-panel animate-pulse rounded-3xl" />
      </div>
    );
  }

  if (days.length === 0) return null;

  const selectedDay = days.find((d) => d.dateStr === selectedDate) || days[0];

  return (
    <div className="max-w-7xl mx-auto w-full px-4 md:px-8 flex flex-col gap-8 md:gap-10 pb-20 mt-6 md:mt-12">
      {/* Detail View */}
      <AnimatePresence mode="wait">
        <motion.div
          key={selectedDate}
          initial={{ opacity: 0, y: 15 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -15 }}
          transition={{ duration: 0.3 }}
          className="flex flex-col gap-6 md:gap-8"
        >
          <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
            <div className="flex flex-col">
              <span className="text-[10px] md:text-xs uppercase font-black tracking-[0.2em] text-accent-cyan/60 mb-1">Detailed Outlook</span>
              <h2 className="text-3xl md:text-5xl font-black text-white tracking-tighter">
                {new Intl.DateTimeFormat('en-US', { weekday: 'long', month: 'long', day: 'numeric' }).format(new Date(selectedDay.dateStr + 'T12:00:00'))}
              </h2>
            </div>
            <div className="glass-panel px-4 py-2 rounded-xl border-white/5 bg-white/2 self-start md:self-end">
              <span className="text-[10px] uppercase font-black text-slate-500 mr-2">Core Engine</span>
              <span className="text-xs font-bold text-accent-cyan">{selectedDay.modelString}</span>
            </div>
          </div>

          {/* Redone Stats Row */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 md:gap-4">
            <StatBox label="Total Snowfall" value={`${selectedDay.totalSnowfall.toFixed(1)} cm`} trend="Model Correction Applied" />
            <StatBox label="Liquid QPF" value={`${selectedDay.totalPrecipitation.toFixed(1)} mm`} trend="Daily Unified Total" />
            <StatBox label="Snow Depth" value={selectedDay.snowDepth || "--"} trend="Estimated Settling" />
            <StatBox label="Solar Events" value={selectedDay.sunrise ? `${selectedDay.sunrise.split('T')[1].substring(0, 5)} / ${selectedDay.sunset?.split('T')[1].substring(0, 5)}` : '--'} trend="Sunrise / Sunset" />
          </div>

          {/* Redone Chart Matrix */}
          <div className="glass-panel rounded-3xl p-3 md:p-5 border border-white/5 relative overflow-hidden flex flex-col gap-2 md:gap-3">
            <HourlySnowChartFromScratch
              day={selectedDay}
              currentHourISO={currentHourISO}
              nowRef={nowRef}
              scrollRef={(el) => {
                scrollRefs.current[0] = el;
                setPrimaryContainer(el);
              }}
              onScroll={(e) => handleScroll(e, 0)}
            />
            <HourlyTempChartFromScratch
              day={selectedDay}
              currentHourISO={currentHourISO}
              scrollRef={(el) => (scrollRefs.current[1] = el)}
              onScroll={(e) => handleScroll(e, 1)}
            />
            <HourlyWindChartFromScratch
              day={selectedDay}
              currentHourISO={currentHourISO}
              scrollRef={(el) => (scrollRefs.current[2] = el)}
              onScroll={(e) => handleScroll(e, 2)}
            />
            <HourlyUVChartFromScratch
              day={selectedDay}
              currentHourISO={currentHourISO}
              scrollRef={(el) => (scrollRefs.current[3] = el)}
              onScroll={(e) => handleScroll(e, 3)}
            />
            <TelemetryRows
              day={selectedDay}
              currentHourISO={currentHourISO}
              scrollRef={(el) => (scrollRefs.current[4] = el)}
              onScroll={(e) => handleScroll(e, 4)}
            />
          </div>
        </motion.div>
      </AnimatePresence>
    </div>
  );
}

function StatBox({ label, value, trend }: { label: string, value: string, trend: string }) {
  return (
    <div className="glass-card p-4 md:p-6 rounded-2xl flex flex-col group border border-white/5 hover:border-white/10 transition-colors">
      <span className="text-[10px] uppercase font-black tracking-widest text-slate-500 mb-2 truncate">{label}</span>
      <span className="text-2xl md:text-3xl font-black text-white mb-2">{value}</span>
      <div className="mt-auto pt-2 border-t border-white/5">
        <span className="text-[9px] font-bold text-slate-600 uppercase tracking-tighter">{trend}</span>
      </div>
    </div>
  );
}

interface ChartRowProps {
  day: DayData;
  currentHourISO: string | null;
  nowRef?: React.RefObject<HTMLDivElement | null>;
  scrollRef: (el: HTMLDivElement | null) => void;
  onScroll: (e: React.UIEvent<HTMLDivElement>) => void;
}

function HourlySnowChartFromScratch({ day, currentHourISO, nowRef, scrollRef, onScroll }: ChartRowProps) {
  const chartHeight = 160;
  // USER: snow bar should max out at 10cm of snowfall
  const safeMax = 10;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <Snowflake className="w-5 h-5 text-accent-cyan" />
          <h3 className="text-xs md:text-sm uppercase font-black tracking-widest text-white">Snowfall Intensity (cm)</h3>
        </div>
        <SlrLegend />
      </div>

      <div className="relative">
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="flex items-end h-[300px] gap-1 md:gap-1.5 overflow-x-auto no-scrollbar relative z-10 pb-10 pt-12 px-4 md:px-8 [overscroll-behavior-x:contain]"
        >
          {day.hourly.map((h, i) => {
            const height = (Math.min(h.snowfall, 10) / safeMax) * chartHeight;
            const hour = parseInt(h.time.split('T')[1].split(':')[0]);
            const isMidnight = hour === 0;
            const isSunrise = day.sunrise && h.time.substring(0, 13) === day.sunrise.substring(0, 13);
            const isSunset = day.sunset && h.time.substring(0, 13) === day.sunset.substring(0, 13);
            const barColor = h.slr ? getSlrColor(h.slr) : "rgba(255, 255, 255, 0.05)";

            const getPeakHue = (h: number) => {
              if (h === 9 || h === 16) return 45; // Yellow
              if (h === 10 || h === 15) return 38; // Golden-Yellow
              if (h === 11 || h === 14) return 32; // Golden-Orange
              if (h === 12 || h === 13) return 25; // Orange
              return null;
            };
            const peakHue = getPeakHue(hour);
            const peakText = peakHue !== null ? `hsl(${peakHue}, 100%, 65%)` : undefined;

            const isCurrent = currentHourISO && h.time.startsWith(currentHourISO);

            return (
              <div
                key={i}
                ref={isCurrent ? nowRef : undefined}
                className={cn(
                  "min-w-[48px] md:min-w-[60px] flex flex-col items-center relative h-full justify-end group/bar",
                  isCurrent && "bg-accent-cyan/[0.03] border-x border-accent-cyan/10"
                )}
              >
                {isCurrent && (
                  <div className="absolute top-1 left-1/2 -translate-x-1/2 bg-accent-cyan text-[8px] font-black px-1.5 py-0.5 rounded-full text-slate-950 z-30 shadow-[0_0_15px_rgba(34,211,238,0.4)] whitespace-nowrap">
                    NOW
                  </div>
                )}
                {/* Solar Indicator Lines */}
                {(isSunrise || isSunset) && (
                  <div className="absolute inset-y-0 left-0 w-[1px] border-l border-dashed border-amber-500/20 z-0 h-full" />
                )}

                {/* Labels */}
                {h.snowfall > 0 && (
                  <div className="absolute z-20 flex flex-col items-center gap-0.5" style={{ bottom: `${height + 38}px` }}>
                    {h.slr && <span className="text-[10px] font-black text-accent-cyan tabular-nums opacity-60">{h.slr.toFixed(0)}:1</span>}
                    <span className="text-[14px] md:text-[16px] font-black text-white tabular-nums drop-shadow-lg">{h.snowfall.toFixed(1)}</span>
                  </div>
                )}

                <div
                  style={{
                    height: `${Math.max(height, h.precipitation > 0 ? 3 : 0)}px`,
                    backgroundColor: h.snowfall > 0 ? barColor : undefined,
                    marginBottom: "36px"
                  }}
                  className={cn(
                    "w-full rounded-t-lg transition-all duration-300",
                    h.snowfall === 0 && h.precipitation > 0 ? "bg-rose-500/40 border-t border-rose-500/50" : (!h.snowfall ? "bg-white/[0.03]" : "")
                  )}
                />

                <div className="absolute bottom-2 flex flex-col items-center">
                  <span
                    style={{ color: peakText }}
                    className={cn(
                      "text-[11px] md:text-[13px] font-black tabular-nums transition-all px-2 py-0.5 rounded-md",
                      isMidnight ? "bg-white text-slate-950" : (peakText ? "" : "text-slate-500")
                    )}
                  >
                    {hour.toString().padStart(2, '0')}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function HourlyTempChartFromScratch({ day, currentHourISO, scrollRef, onScroll }: ChartRowProps) {
  const temps = day.hourly.map(h => h.temperature).filter(t => t !== null) as number[];
  const rawMin = Math.min(...temps, 0);
  const rawMax = Math.max(...temps, 0);
  const rawRange = rawMax - rawMin;
  const minTemp = rawMin - 2;
  const maxTemp = rawMax + Math.max(6, rawRange * 0.25);
  const range = maxTemp - minTemp;
  const chartHeight = 160;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <Thermometer className="w-5 h-5 text-rose-400" />
          <h3 className="text-xs md:text-sm uppercase font-black tracking-widest text-white">Temperature Trend (°C)</h3>
        </div>
      </div>

      <div className="relative">
        {/* Y-Axis Labels aligned with chart baseline */}
        <div
          className="absolute -left-2 md:-left-4 h-full flex flex-col pointer-events-none z-0 text-[10px] font-black text-slate-700"
          style={{ bottom: "46px", height: `${chartHeight}px` }}
        >
          <span className="flex items-center h-4">{maxTemp.toFixed(0)}°</span>
          <div className="flex-grow flex items-center" style={{ height: "0px" }}>
            <span className="translate-y-[-50%]" style={{ position: 'absolute', bottom: `${((0 - minTemp) / range) * chartHeight}px` }}>0°</span>
          </div>
          <span className="flex items-center h-4 mt-auto">{minTemp.toFixed(0)}°</span>
        </div>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="flex items-end h-[300px] gap-1 md:gap-1.5 overflow-x-auto no-scrollbar relative z-10 pb-10 pt-12 px-4 md:px-8 [overscroll-behavior-x:contain]"
        >
          {day.hourly.map((h, i) => {
            const temp = h.temperature ?? 0;
            const pos = ((temp - minTemp) / range) * chartHeight;
            const isFreezing = temp <= 0;
            const hour = parseInt(h.time.split('T')[1].split(':')[0]);
            const isMidnight = hour === 0;

            const getPeakHue = (h: number) => {
              if (h === 9 || h === 16) return 45; // Yellow
              if (h === 10 || h === 15) return 38; // Golden-Yellow
              if (h === 11 || h === 14) return 32; // Golden-Orange
              if (h === 12 || h === 13) return 25; // Orange
              return null;
            };
            const peakHue = getPeakHue(hour);
            const peakText = peakHue !== null ? `hsl(${peakHue}, 100%, 65%)` : undefined;

            const isCurrent = currentHourISO && h.time.startsWith(currentHourISO);
            return (
              <div
                key={i}
                className={cn(
                  "min-w-[48px] md:min-w-[60px] flex flex-col items-center h-full justify-end relative group",
                  isCurrent && "bg-rose-400/[0.03] border-x border-rose-400/10"
                )}
              >
                {isCurrent && (
                  <div className="absolute top-1 left-1/2 -translate-x-1/2 bg-rose-400 text-[8px] font-black px-1.5 py-0.5 rounded-full text-slate-950 z-30 shadow-[0_0_15px_rgba(251,113,133,0.4)] whitespace-nowrap">
                    NOW
                  </div>
                )}
                <div
                  style={{ bottom: `${pos + 36 + 10}px` }}
                  className={cn(
                    "absolute w-2.5 h-2.5 rounded-full transition-transform duration-500 group-hover:scale-125 z-10 shadow-lg",
                    isFreezing ? "bg-blue-400 shadow-blue-500/40" : "bg-rose-400 shadow-rose-500/40"
                  )}
                />

                <span
                  style={{ bottom: `${pos + 36 + 28}px` }}
                  className={cn(
                    "absolute text-[12px] md:text-[14px] font-black tabular-nums transition-opacity",
                    isFreezing ? "text-blue-300" : "text-rose-300"
                  )}
                >
                  {temp.toFixed(0)}°
                </span>

                <div className="w-[1px] h-full bg-white/[0.03] z-0" />
                <div className="absolute w-full h-[1px] bg-white/10 z-0" style={{ bottom: `${((0 - minTemp) / range) * chartHeight + 36 + 10}px` }} />

                <div className="absolute bottom-2 flex flex-col items-center">
                  <span
                    style={{ color: peakText }}
                    className={cn(
                      "text-[11px] md:text-[13px] font-black tabular-nums transition-all px-2 py-0.5 rounded-md",
                      isMidnight ? "bg-white text-slate-950" : (peakText ? "" : "text-slate-500")
                    )}
                  >
                    {hour.toString().padStart(2, '0')}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function HourlyWindChartFromScratch({ day, currentHourISO, scrollRef, onScroll }: ChartRowProps) {
  const chartHeight = 120;
  // Convert m/s to km/h and find max for scaling
  const getSpeedKmH = (ms: number | null) => (ms != null ? ms * 3.6 : 0);

  const allSpeeds = day.hourly.flatMap(h => [getSpeedKmH(h.windSpeed), getSpeedKmH(h.gusts)]);
  const maxSpeed = Math.max(...allSpeeds, 20); // Min scale of 20 km/h
  const safeMax = Math.ceil(maxSpeed / 10) * 10;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <Wind className="w-5 h-5 text-emerald-400" />
          <h3 className="text-xs md:text-sm uppercase font-black tracking-widest text-white">Wind & Gusts (km/h)</h3>
        </div>
      </div>

      <div className="relative">
        {/* Y-Axis Labels */}
        <div
          className="absolute -left-2 md:-left-4 h-full flex flex-col pointer-events-none z-0 text-[10px] font-black text-slate-700"
          style={{ bottom: "46px", height: `${chartHeight}px` }}
        >
          <span className="flex items-center h-4">{safeMax}</span>
          <span className="flex items-center h-4 mt-auto">0</span>
        </div>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="flex items-end h-[300px] gap-1 md:gap-1.5 overflow-x-auto no-scrollbar relative z-10 pb-10 pt-12 px-4 md:px-8 [overscroll-behavior-x:contain]"
        >
          {day.hourly.map((h, i) => {
            const speed = getSpeedKmH(h.windSpeed);
            const gust = getSpeedKmH(h.gusts);
            const speedHeight = (speed / safeMax) * chartHeight;
            const gustHeight = (gust / safeMax) * chartHeight;

            const hour = parseInt(h.time.split('T')[1].split(':')[0]);
            const isMidnight = hour === 0;
            const isCurrent = currentHourISO && h.time.startsWith(currentHourISO);

            return (
              <div
                key={i}
                className={cn(
                  "min-w-[48px] md:min-w-[60px] flex flex-col items-center h-full justify-end relative group",
                  isCurrent && "bg-emerald-400/[0.03] border-x border-emerald-400/10"
                )}
              >
                {isCurrent && (
                  <div className="absolute top-1 left-1/2 -translate-x-1/2 bg-emerald-400 text-[8px] font-black px-1.5 py-0.5 rounded-full text-slate-950 z-30 shadow-[0_0_15px_rgba(52,211,153,0.4)] whitespace-nowrap">
                    NOW
                  </div>
                )}

                {/* Wind Direction Arrow */}
                <div
                  className="absolute z-20 transition-transform duration-500"
                  style={{
                    bottom: `${Math.max(gustHeight, speedHeight) + 68}px`,
                    transform: `rotate(${(h.windDir || 0) + 180}deg)`
                  }}
                >
                  <Navigation2 className="w-4 h-4 text-emerald-300 shadow-sm" style={{ fill: 'currentColor' }} />
                </div>

                {/* Gust Bar (Semi-transparent, taller) */}
                <div
                  style={{
                    height: `${gustHeight}px`,
                    width: '100%',
                    bottom: '36px'
                  }}
                  className="absolute bg-emerald-400/15 rounded-t-md z-0"
                />

                {/* Speed Bar */}
                <div
                  style={{
                    height: `${speedHeight}px`,
                    width: '100%',
                    bottom: '36px'
                  }}
                  className="absolute bg-emerald-500/40 rounded-t-md z-10 border-t border-emerald-400/30"
                />

                {/* Permanent Labels */}
                {gust > 0 && (
                  <div className="absolute z-20 flex flex-col items-center" style={{ bottom: `${gustHeight + 38}px` }}>
                    <span className="text-[11px] md:text-[13px] font-black text-emerald-300 tabular-nums drop-shadow-lg">
                      {gust.toFixed(0)}<span className="text-[8px] opacity-60 ml-0.5">km/h</span>
                    </span>
                  </div>
                )}

                {speed > 0 && Math.abs(gustHeight - speedHeight) > 16 && (
                  <div className="absolute z-20 flex flex-col items-center" style={{ bottom: `${speedHeight + 38}px` }}>
                    <span className="text-[10px] md:text-[11px] font-black text-white/80 tabular-nums drop-shadow-md">
                      {speed.toFixed(0)}<span className="text-[8px] opacity-60 ml-0.5">km/h</span>
                    </span>
                  </div>
                )}

                <div className="absolute bottom-2 flex flex-col items-center">
                  <span
                    className={cn(
                      "text-[11px] md:text-[13px] font-black tabular-nums transition-all px-2 py-0.5 rounded-md",
                      isMidnight ? "bg-white text-slate-950" : "text-slate-500"
                    )}
                  >
                    {hour.toString().padStart(2, '0')}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function HourlyUVChartFromScratch({ day, currentHourISO, scrollRef, onScroll }: ChartRowProps) {
  const chartHeight = 100;
  const safeMax = 12; // Standard UV scale usually goes up to 11+

  const getUvColor = (uv: number) => {
    if (uv <= 2) return "bg-green-500/40 border-green-400/30";
    if (uv <= 5) return "bg-yellow-400/40 border-yellow-300/30";
    if (uv <= 7) return "bg-orange-500/40 border-orange-400/30";
    if (uv <= 10) return "bg-red-500/40 border-red-400/30";
    return "bg-violet-500/40 border-violet-400/30";
  };

  const getUvTextClass = (uv: number) => {
    if (uv <= 2) return "text-green-300";
    if (uv <= 5) return "text-yellow-300";
    if (uv <= 7) return "text-orange-300";
    if (uv <= 10) return "text-red-300";
    return "text-violet-300";
  };

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <Sun className="w-5 h-5 text-yellow-400" />
          <h3 className="text-xs md:text-sm uppercase font-black tracking-widest text-white">UV Index</h3>
        </div>
      </div>

      <div className="relative">
        {/* Y-Axis Labels */}
        <div
          className="absolute -left-2 md:-left-4 h-full flex flex-col pointer-events-none z-0 text-[10px] font-black text-slate-700"
          style={{ bottom: "46px", height: `${chartHeight}px` }}
        >
          <span className="flex items-center h-4">12</span>
          <span className="flex items-center h-4 mt-auto">0</span>
        </div>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="flex items-end h-[240px] gap-1 md:gap-1.5 overflow-x-auto no-scrollbar relative z-10 pb-10 pt-12 px-4 md:px-8 [overscroll-behavior-x:contain]"
        >
          {day.hourly.map((h, i) => {
            const uv = h.uvIndex ?? 0;
            const height = (Math.min(uv, safeMax) / safeMax) * chartHeight;

            const hour = parseInt(h.time.split('T')[1].split(':')[0]);
            const isMidnight = hour === 0;
            const isCurrent = currentHourISO && h.time.startsWith(currentHourISO);

            return (
              <div
                key={i}
                className={cn(
                  "min-w-[48px] md:min-w-[60px] flex flex-col items-center h-full justify-end relative group",
                  isCurrent && "bg-yellow-400/[0.03] border-x border-yellow-400/10"
                )}
              >
                {isCurrent && (
                  <div className="absolute top-1 left-1/2 -translate-x-1/2 bg-yellow-400 text-[8px] font-black px-1.5 py-0.5 rounded-full text-slate-950 z-30 shadow-[0_0_15px_rgba(250,204,21,0.4)] whitespace-nowrap">
                    NOW
                  </div>
                )}

                {/* UV Bar */}
                <div
                  style={{
                    height: `${Math.max(height, uv > 0 ? 3 : 0)}px`,
                    width: '100%',
                    bottom: '36px'
                  }}
                  className={cn(
                    "absolute rounded-t-lg z-10 border-t transition-all",
                    getUvColor(uv)
                  )}
                />

                {/* Label */}
                {uv > 0 && (
                  <div className="absolute z-20 flex flex-col items-center" style={{ bottom: `${height + 38}px` }}>
                    <span className={cn("text-[12px] md:text-[14px] font-black tabular-nums drop-shadow-lg", getUvTextClass(uv))}>
                      {uv.toFixed(1)}
                    </span>
                  </div>
                )}

                <div className="absolute bottom-2 flex flex-col items-center">
                  <span
                    className={cn(
                      "text-[11px] md:text-[13px] font-black tabular-nums transition-all px-2 py-0.5 rounded-md",
                      isMidnight ? "bg-white text-slate-950" : "text-slate-500"
                    )}
                  >
                    {hour.toString().padStart(2, '0')}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function TelemetryRows({ day, currentHourISO, scrollRef, onScroll }: { day: DayData, currentHourISO: string | null, scrollRef: (el: HTMLDivElement | null) => void, onScroll: (e: React.UIEvent<HTMLDivElement>) => void }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2 px-1">
        <Info className="w-5 h-5 text-slate-500" />
        <h3 className="text-xs md:text-sm uppercase font-black tracking-widest text-white">Advanced Telemetry</h3>
      </div>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex overflow-x-auto no-scrollbar gap-1 md:gap-1.5 pb-4 px-4 md:px-8 [overscroll-behavior-x:contain]"
      >
        {day.hourly.map((h, i) => {
          const hour = parseInt(h.time.split('T')[1].split(':')[0]);
          const isMidnight = hour === 0;
          const isCurrent = currentHourISO && h.time.startsWith(currentHourISO);
          return (
            <div
              key={i}
              className={cn(
                "min-w-[48px] md:min-w-[60px] flex flex-col gap-3 items-center bg-white/[0.02] py-4 rounded-xl transition-colors",
                isCurrent ? "bg-accent-cyan/[0.08] ring-1 ring-inset ring-accent-cyan/20" : "hover:bg-white/[0.04]"
              )}
            >
              <div className={cn(
                "px-2 py-0.5 rounded text-[10px] font-black tabular-nums mb-1",
                isMidnight ? "bg-white text-slate-950" : "text-slate-500"
              )}>
                {hour.toString().padStart(2, '0')}
              </div>
              <MetricPill label="RH" value={h.rh != null ? `${h.rh.toFixed(0)}%` : '--'} color="cyan" />
              <MetricPill label="CLD" value={h.clouds != null ? `${h.clouds.toFixed(0)}%` : '--'} color="slate" />
              <MetricPill label="LVL" value={h.snowLevel != null ? `${(h.snowLevel / 1000).toFixed(1)}k` : '--'} color="blue" />
              <MetricPill label="FLS" value={h.feelsLike != null ? `${h.feelsLike.toFixed(0)}°` : '--'} color="rose" />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function MetricPill({ label, value, color }: { label: string, value: string, color: string }) {
  const colorMap = {
    cyan: "text-cyan-400",
    slate: "text-slate-400",
    blue: "text-blue-400",
    rose: "text-rose-400"
  }[color];

  return (
    <div className="flex flex-col items-center">
      <span className="text-[7px] font-black uppercase tracking-tighter text-slate-600 mb-0.5">{label}</span>
      <span className={cn("text-[10px] md:text-[11px] font-black tabular-nums", colorMap)}>{value}</span>
    </div>
  );
}

function SlrLegend() {
  const stops = [5, 7.5, 10, 12.5, 15];
  const gradientStops = stops.map(s => getSlrColor(s)).join(', ');

  return (
    <div className="flex flex-col gap-1.5 items-end px-1">
      <div
        style={{ background: `linear-gradient(to right, ${gradientStops})` }}
        className="h-1.5 w-28 md:w-36 rounded-full relative shadow-[0_0_10px_rgba(255,255,255,0.05)]"
      >
        <div className="absolute inset-0 rounded-full border border-white/10" />
      </div>
      <div className="flex justify-between w-28 md:w-36 px-0.5">
        <span className="text-[8px] md:text-[9px] font-black uppercase text-slate-500 tracking-tighter">Wet</span>
        <span className="text-[8px] md:text-[9px] font-black uppercase text-slate-500 tracking-tighter">Norm</span>
        <span className="text-[8px] md:text-[9px] font-black uppercase text-slate-500 tracking-tighter text-right">Pow</span>
      </div>
    </div>
  );
}
