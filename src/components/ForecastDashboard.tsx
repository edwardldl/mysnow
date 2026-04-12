"use client";

import React, { useRef } from "react";
import { Snowflake, Thermometer, Info } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { cn } from "@/lib/utils_tailwind";
import { DayData } from "@/lib/types";
import { getSlrColor } from "@/lib/utils";

interface ForecastDashboardProps {
  days: DayData[];
  isLoading: boolean;
  selectedDate: string | null;
  setSelectedDate: (date: string) => void;
}

export default function ForecastDashboard({ days, isLoading, selectedDate }: ForecastDashboardProps) {
  const scrollRefs = useRef<(HTMLDivElement | null)[]>([]);

  // Synchronize scrolling across multiple chart containers
  const handleScroll = (e: React.UIEvent<HTMLDivElement>, index: number) => {
    const scrollLeft = (e.target as HTMLDivElement).scrollLeft;
    scrollRefs.current.forEach((ref, i) => {
      if (ref && i !== index) {
        ref.scrollLeft = scrollLeft;
      }
    });
  };

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
            <StatBox label="Snow Depth" value={selectedDay.snowDepth} trend="Estimated Settling" />
            <StatBox label="Solar Events" value={selectedDay.sunrise ? `${selectedDay.sunrise.split('T')[1].substring(0, 5)} / ${selectedDay.sunset?.split('T')[1].substring(0, 5)}` : '--'} trend="Sunrise / Sunset" />
          </div>

          {/* Redone Chart Matrix */}
          <div className="glass-panel rounded-3xl p-4 md:p-10 border border-white/5 relative overflow-hidden flex flex-col gap-10 md:gap-14">
            <HourlySnowChartFromScratch
              day={selectedDay}
              scrollRef={(el) => (scrollRefs.current[0] = el)}
              onScroll={(e) => handleScroll(e, 0)}
            />
            <HourlyTempChartFromScratch
              day={selectedDay}
              scrollRef={(el) => (scrollRefs.current[1] = el)}
              onScroll={(e) => handleScroll(e, 1)}
            />
            <TelemetryRows 
              day={selectedDay} 
              scrollRef={(el) => (scrollRefs.current[2] = el)}
              onScroll={(e) => handleScroll(e, 2)}
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
  scrollRef: (el: HTMLDivElement | null) => void;
  onScroll: (e: React.UIEvent<HTMLDivElement>) => void;
}

function HourlySnowChartFromScratch({ day, scrollRef, onScroll }: ChartRowProps) {
  const chartHeight = 160;
  // USER: snow bar should max out at 10cm of snowfall
  const safeMax = 10; 

  return (
    <div className="flex flex-col gap-8">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <Snowflake className="w-5 h-5 text-accent-cyan" />
          <h3 className="text-xs md:text-sm uppercase font-black tracking-widest text-white">Snowfall Intensity (cm)</h3>
        </div>
      </div>

      <div className="relative">
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="flex items-end h-[320px] gap-1 md:gap-1.5 overflow-x-auto no-scrollbar relative z-10 pb-16 pt-10"
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

            return (
              <div 
                key={i} 
                className="min-w-[48px] md:min-w-[60px] flex flex-col items-center relative h-full justify-end group/bar"
              >
                {/* Solar Indicator Lines */}
                {(isSunrise || isSunset) && (
                  <div className="absolute inset-y-0 left-0 w-[1px] border-l border-dashed border-amber-500/20 z-0 h-full" />
                )}

                {/* Labels */}
                {h.snowfall > 0 && (
                  <div className="absolute z-20 flex flex-col items-center gap-0.5" style={{ bottom: `${height + 72}px` }}>
                    {h.slr && <span className="text-[10px] font-black text-accent-cyan tabular-nums opacity-60">{h.slr.toFixed(0)}</span>}
                    <span className="text-[14px] md:text-[16px] font-black text-white tabular-nums drop-shadow-lg">{h.snowfall.toFixed(1)}</span>
                  </div>
                )}

                <div
                  style={{
                    height: `${Math.max(height, h.precipitation > 0 ? 3 : 0)}px`,
                    backgroundColor: h.snowfall > 0 ? barColor : undefined,
                    marginBottom: "56px"
                  }}
                  className={cn(
                    "w-full rounded-t-lg transition-all duration-300",
                    h.snowfall === 0 && h.precipitation > 0 ? "bg-rose-500/40 border-t border-rose-500/50" : (!h.snowfall ? "bg-white/[0.03]" : "")
                  )}
                />

                <div className="absolute bottom-4 flex flex-col items-center">
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

function HourlyTempChartFromScratch({ day, scrollRef, onScroll }: ChartRowProps) {
  const temps = day.hourly.map(h => h.temperature).filter(t => t !== null) as number[];
  const minTemp = Math.min(...temps, 0) - 2;
  const maxTemp = Math.max(...temps, 0) + 2;
  const range = maxTemp - minTemp;
  const chartHeight = 180;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex items-center justify-between px-1">
        <div className="flex items-center gap-2">
          <Thermometer className="w-5 h-5 text-rose-400" />
          <h3 className="text-xs md:text-sm uppercase font-black tracking-widest text-white">Temperature Trend (°C)</h3>
        </div>
      </div>

      <div className="relative">
        {/* Y-Axis Labels */}
        <div className="absolute -left-2 md:-left-4 top-0 h-[220px] flex flex-col justify-between text-[9px] font-black text-slate-700 pointer-events-none z-0">
          <span>{maxTemp.toFixed(0)}°</span>
          <span>0°</span>
          <span>{minTemp.toFixed(0)}°</span>
        </div>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="flex items-end h-[320px] gap-1 md:gap-1.5 overflow-x-auto no-scrollbar relative z-10 pb-16 pl-6 md:pl-0 pt-10"
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

            return (
              <div 
                key={i} 
                className="min-w-[48px] md:min-w-[60px] flex flex-col items-center h-full justify-end relative group"
              >
                <div
                  style={{ bottom: `${pos + 56 + 10}px` }}
                  className={cn(
                    "absolute w-2.5 h-2.5 rounded-full transition-transform duration-500 group-hover:scale-125 z-10 shadow-lg",
                    isFreezing ? "bg-blue-400 shadow-blue-500/40" : "bg-rose-400 shadow-rose-500/40"
                  )}
                />

                <span
                  style={{ bottom: `${pos + 56 + 28}px` }}
                  className={cn(
                    "absolute text-[12px] md:text-[14px] font-black tabular-nums transition-opacity",
                    isFreezing ? "text-blue-300" : "text-rose-300"
                  )}
                >
                  {temp.toFixed(0)}°
                </span>

                <div className="w-[1px] h-full bg-white/[0.03] z-0" />
                <div className="absolute w-full h-[1px] bg-white/10 z-0" style={{ bottom: `${((0 - minTemp) / range) * chartHeight + 56 + 10}px` }} />

                <div className="absolute bottom-4 flex flex-col items-center">
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

function TelemetryRows({ day, scrollRef, onScroll }: { day: DayData, scrollRef: (el: HTMLDivElement | null) => void, onScroll: (e: React.UIEvent<HTMLDivElement>) => void }) {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-2 px-1">
        <Info className="w-5 h-5 text-slate-500" />
        <h3 className="text-xs md:text-sm uppercase font-black tracking-widest text-white">Advanced Telemetry</h3>
      </div>
      <div 
        ref={scrollRef}
        onScroll={onScroll}
        className="flex overflow-x-auto no-scrollbar gap-1 md:gap-1.5 pb-4"
      >
        {day.hourly.map((h, i) => (
          <div key={i} className="min-w-[48px] md:min-w-[60px] flex flex-col gap-4 items-center bg-white/[0.02] py-4 rounded-xl">
            <MetricPill label="RH" value={h.rh != null ? `${h.rh.toFixed(0)}%` : '--'} color="cyan" />
            <MetricPill label="CLD" value={h.clouds != null ? `${h.clouds.toFixed(0)}%` : '--'} color="slate" />
            <MetricPill label="LVL" value={h.snowLevel != null ? `${(h.snowLevel / 1000).toFixed(1)}k` : '--'} color="blue" />
            <MetricPill label="FLS" value={h.feelsLike != null ? `${h.feelsLike.toFixed(0)}°` : '--'} color="rose" />
          </div>
        ))}
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
