"use client";

import React from "react";
import { 
  Thermometer, 
  Wind, 
  Snowflake, 
  Sun, 
  Eye, 
  Navigation2,
  Clock,
  Waves,
  CloudRain
} from "lucide-react";
import { cn } from "@/lib/utils_tailwind";
import { BlendedHour, RollingStats } from "@/lib/types";
import { getWeatherDescription } from "@/lib/utils";
import { motion } from "framer-motion";

interface CurrentWeatherCardProps {
  currentData: (BlendedHour & { minTemp?: number; maxTemp?: number }) | null;
  rollingStats: RollingStats | null;
  locationName: string;
  isDaily?: boolean;
  className?: string;
}

export default function CurrentWeatherCard({ 
  currentData, 
  rollingStats, 
  locationName, 
  isDaily = false,
  className 
}: CurrentWeatherCardProps) {
  const weather = currentData ? getWeatherDescription(currentData.weatherCode) : null;
  
  if (!currentData) return null;

  return (
    <motion.div 
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
      className={cn("w-full max-w-7xl mx-auto px-4 md:px-8 py-4 md:py-8", className)}
    >
      <div className="relative glass-panel rounded-[2.5rem] overflow-hidden border-white/10 shadow-3xl bg-slate-950/40 backdrop-blur-3xl">
        
        {/* Animated Background Accents - More Vibrant */}
        <div className="absolute top-[-20%] right-[-10%] w-[50%] h-[60%] bg-accent-blue/20 rounded-full blur-[120px] pointer-events-none -z-10 animate-pulse-soft" />
        <div className="absolute bottom-[-20%] left-[-10%] w-[50%] h-[60%] bg-accent-cyan/10 rounded-full blur-[120px] pointer-events-none -z-10 animate-pulse-soft" style={{ animationDelay: '2s' }} />

        <div className="relative p-8 md:p-12 flex flex-col xl:flex-row gap-12 xl:items-center">
          
          {/* Section 1: The Hero */}
          <div className="flex-1 flex flex-col md:flex-row md:items-center gap-8 md:gap-12">
            <motion.div 
              whileHover={{ scale: 1.05, rotate: 5 }}
              className="flex items-center justify-center w-32 h-32 md:w-44 md:h-44 rounded-[2.5rem] bg-white/5 border border-white/10 shadow-2xl backdrop-blur-md relative group"
            >
              <div className="absolute inset-0 bg-accent-blue/10 rounded-[2.5rem] opacity-0 group-hover:opacity-100 transition-opacity blur-xl" />
              <span className="text-7xl md:text-9xl drop-shadow-2xl relative z-10 select-none">
                {weather?.icon || "—"}
              </span>
            </motion.div>
            
            <div className="flex flex-col">
              <div className="flex items-center gap-3 mb-3">
                <span className={cn(
                    "px-3 py-1 rounded-full text-white text-[10px] font-black uppercase tracking-widest shadow-lg",
                    isDaily ? "bg-slate-700 shadow-slate-900/20" : "bg-accent-blue shadow-blue-500/20"
                )}>
                    {isDaily ? "Daily Summary" : "Live Observation"}
                </span>
                <span className="text-slate-400 text-xs font-bold flex items-center gap-1.5 opacity-80">
                   <Clock className="w-3.5 h-3.5" /> 
                   {isDaily 
                    ? new Date(currentData.time).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })
                    : new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                   }
                </span>
              </div>
              
              <h2 className="text-3xl md:text-5xl font-black text-white tracking-tighter mb-2">
                {locationName}
              </h2>
              
              <div className="flex items-baseline gap-6">
                <span className="text-7xl md:text-9xl font-black text-white tracking-tighter drop-shadow-sm">
                  {isDaily && currentData.minTemp !== undefined && currentData.maxTemp !== undefined ? (
                    <div className="flex flex-col md:flex-row md:items-baseline gap-2 md:gap-4">
                        <span className="text-rose-400">{currentData.maxTemp.toFixed(0)}°</span>
                        <span className="text-4xl md:text-6xl text-slate-500 font-black">/</span>
                        <span className="text-5xl md:text-7xl text-blue-400">{currentData.minTemp.toFixed(0)}°</span>
                    </div>
                  ) : (
                    <>
                        {currentData.temperature.toFixed(1)}<span className="text-accent-blue">°</span>
                    </>
                  )}
                </span>
                <div className="flex flex-col mb-2 md:mb-4">
                  <span className="text-xl md:text-3xl font-bold text-slate-200 tracking-tight leading-none mb-2">
                    {weather?.label}
                  </span>
                  {!isDaily && (
                    <div className="flex flex-col gap-1.5">
                        <div className="flex items-center gap-2 text-slate-400 font-bold text-sm md:text-base uppercase tracking-tight">
                            <Thermometer className="w-4 h-4 text-rose-400" />
                            Feels like <span className="text-white">{currentData.feelsLike?.toFixed(1)}°</span>
                        </div>
                        {currentData.minTemp !== undefined && currentData.maxTemp !== undefined && (
                            <div className="flex items-center gap-3 text-[10px] md:text-xs font-black uppercase tracking-widest">
                                <span className="text-rose-400/80">H: <span className="text-white">{currentData.maxTemp.toFixed(0)}°</span></span>
                                <span className="text-blue-400/80">L: <span className="text-white">{currentData.minTemp.toFixed(0)}°</span></span>
                            </div>
                        )}
                    </div>
                  )}
                  {isDaily && (
                    <div className="flex items-center gap-2 text-slate-400 font-bold text-sm md:text-base uppercase tracking-tight">
                        <Snowflake className="w-4 h-4 text-accent-cyan" />
                        Total Snow <span className="text-white">{(currentData.snowfall || 0).toFixed(1)} cm</span>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Section 2: Metrics Matrix */}
          <div className="flex flex-col gap-8 md:flex-row xl:flex-col xl:w-auto">
            
            {/* Live Atmosphere Group */}
            <div className="flex flex-col gap-4">
                <h3 className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] ml-1">
                    {isDaily ? "Day Atmosphere" : "Live Atmosphere"}
                </h3>
                <div className="grid grid-cols-3 gap-3">
                  <CompactStatBox 
                      icon={<Wind className="w-3.5 h-3.5 text-emerald-400" />}
                      label="Wind"
                      value={`${(currentData.windSpeed! * 3.6).toFixed(0)}`}
                      unit="km/h"
                      subValue={currentData.gusts != null && currentData.gusts > (currentData.windSpeed || 0) ? `G:${(currentData.gusts * 3.6).toFixed(0)}` : undefined}
                      extra={
                          <div className="flex items-center gap-1 mt-1">
                              <Navigation2 
                                  className="w-3 h-3 text-emerald-400"
                                  style={{ transform: `rotate(${(currentData.windDir || 0) + 180}deg)`, fill: 'currentColor' }}
                              />
                              <span className="text-[8px] font-bold text-emerald-400/60 lowercase tracking-tighter">
                                  {((currentData.windDir || 0))}°
                              </span>
                          </div>
                      }
                  />
                  <CompactStatBox 
                      icon={<Sun className="w-3.5 h-3.5 text-yellow-400" />}
                      label="UV Index"
                      value={currentData.uvIndex?.toFixed(1) || "0.0"}
                      unit={currentData.uvIndex != null && currentData.uvIndex > 5 ? "High" : "Low"}
                  />
                  <CompactStatBox 
                      icon={<Eye className="w-3.5 h-3.5 text-violet-400" />}
                      label="Visibility"
                      value={currentData.visibility != null ? (currentData.visibility / 1000).toFixed(1) : "--"}
                      unit="km"
                  />
                </div>
            </div>

            {/* Snow Accumulation Group */}
            <div className="flex flex-col gap-4">
                <h3 className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] ml-1">
                    {isDaily ? "Snow History" : "Snow Accumulation"}
                </h3>
                <div className="grid grid-cols-3 gap-3">
                    <CompactStatBox 
                        icon={<Snowflake className="w-3.5 h-3.5 text-accent-cyan" />}
                        label={isDaily ? "Daily Snow" : "Rate"}
                        value={currentData.snowfall.toFixed(1)}
                        unit={isDaily ? "cm" : "cm/h"}
                    />
                    {rollingStats && (
                        <>
                            <CompactStatBox 
                                icon={<Waves className="w-3.5 h-3.5 text-blue-400" />}
                                label={isDaily ? "Prior 24h" : "24h Snow"}
                                value={rollingStats.snow24h.toFixed(1)}
                                unit="cm"
                                subValue={rollingStats.slr24h != null ? `${rollingStats.slr24h.toFixed(0)}:1` : undefined}
                            />
                            <CompactStatBox 
                                icon={<CloudRain className="w-3.5 h-3.5 text-indigo-400" />}
                                label={isDaily ? "Prior 48h" : "48h Snow"}
                                value={rollingStats.snow48h.toFixed(1)}
                                unit="cm"
                                subValue={rollingStats.slr48h != null ? `${rollingStats.slr48h.toFixed(0)}:1` : undefined}
                            />
                        </>
                    )}
                </div>
            </div>

          </div>

        </div>
      </div>
    </motion.div>
  );
}

function CompactStatBox({ icon, label, value, unit, subValue, extra }: { 
  icon: React.ReactNode, 
  label: string, 
  value: string, 
  unit?: string,
  subValue?: string,
  extra?: React.ReactNode
}) {
  return (
    <div className="glass-card p-4 rounded-2xl flex flex-col border border-white/5 bg-white/[0.02] hover:bg-white/[0.05] transition-all duration-300 min-w-[100px] md:min-w-[120px]">
      <div className="flex items-center gap-2 mb-2">
        <div className="p-1 rounded-lg bg-white/5">{icon}</div>
        <span className="text-[9px] font-black text-slate-500 uppercase tracking-widest truncate">{label}</span>
      </div>
      <div className="flex items-baseline gap-1">
        <span className="text-xl font-black text-white tracking-tight">{value}</span>
        {unit && <span className="text-[10px] font-bold text-slate-500">{unit}</span>}
      </div>
      {subValue && (
        <span className="text-[9px] font-black text-accent-cyan/60 uppercase mt-1 tabular-nums leading-none mb-1">{subValue}</span>
      )}
      {extra}
    </div>
  );
}
