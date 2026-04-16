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
import { BlendedHour, RollingStats, WeatherDataStatus } from "@/lib/types";
import { getWeatherDescription } from "@/lib/utils";
import { motion } from "framer-motion";

interface CurrentWeatherCardProps {
  currentData: (BlendedHour & { minTemp?: number; maxTemp?: number }) | null;
  rollingStats: RollingStats | null;
  locationName: string;
  latitude?: number;
  longitude?: number;
  isDaily?: boolean;
  timezone?: string;
  dataStatus?: WeatherDataStatus;
  className?: string;
}

export default function CurrentWeatherCard({ 
  currentData, 
  rollingStats, 
  locationName, 
  latitude,
  longitude,
  isDaily = false,
  timezone = 'America/Los_Angeles',
  dataStatus = 'fresh',
  className 
}: CurrentWeatherCardProps) {
  const weather = currentData ? getWeatherDescription(currentData.weatherCode) : null;

  const formatCoord = (val: number | undefined, isLat: boolean) => {
    if (val === undefined) return null;
    const abs = Math.abs(val).toFixed(3);
    const dir = isLat ? (val >= 0 ? 'N' : 'S') : (val >= 0 ? 'E' : 'W');
    return `${abs}°${dir}`;
  };
  
  if (!currentData) return null;

  return (
    <motion.div 
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
      className={cn("w-full max-w-7xl mx-auto px-2 md:px-8 py-2 md:pt-3 md:pb-3", className)}
    >
      <div className="relative glass-panel rounded-[1.5rem] md:rounded-[2.5rem] overflow-hidden border-white/10 shadow-3xl bg-slate-950/40 backdrop-blur-3xl">
        
        {/* Animated Background Accents - More Vibrant */}
        <div className="absolute top-[-20%] right-[-10%] w-[50%] h-[60%] bg-accent-blue/20 rounded-full blur-[120px] pointer-events-none -z-10 animate-pulse-soft" />
        <div className="absolute bottom-[-20%] left-[-10%] w-[50%] h-[60%] bg-accent-cyan/10 rounded-full blur-[120px] pointer-events-none -z-10 animate-pulse-soft" style={{ animationDelay: '2s' }} />

        <div className="relative p-4 md:pt-6 md:pb-8 md:px-8 flex flex-col xl:flex-row gap-6 md:gap-8 xl:items-center">
          
          {/* Section 1: The Hero */}
          <div className="flex-1 flex flex-row items-center gap-4 md:gap-10">
            <motion.div 
              whileHover={{ scale: 1.1, rotate: 5 }}
              className="flex items-center justify-center shrink-0 w-16 h-16 md:w-32 md:h-32 relative group"
            >
              <span className={cn(
                "drop-shadow-2xl relative z-10 select-none",
                (weather?.icon?.length || 0) > 2 
                  ? "text-3xl md:text-7xl" 
                  : "text-4xl md:text-8xl"
              )}>
                {weather?.icon || "—"}
              </span>
            </motion.div>
            
            <div className="flex flex-col min-w-0">
              <div className="flex items-center flex-wrap gap-2 mb-1 md:mb-3">

                <span className="text-slate-400 text-[10px] md:text-xs font-bold flex items-center gap-1 opacity-80">
                   <Clock className="w-3 h-3 md:w-3.5 md:h-3.5" /> 
                   {isDaily 
                    ? new Date(currentData.time).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
                    : new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', timeZone: timezone })
                   }
                </span>
              </div>
              
              <h2 className="text-xl md:text-4xl font-black text-white tracking-tighter truncate">
                {locationName}
              </h2>
              
              {(latitude !== undefined && longitude !== undefined) && (
                <div className="flex items-center gap-2 text-slate-500 font-bold text-[10px] md:text-xs uppercase tracking-widest opacity-70 mb-1 md:mb-2">
                  <span>{formatCoord(latitude, true)}</span>
                  <span className="w-1 h-1 rounded-full bg-slate-700" />
                  <span>{formatCoord(longitude, false)}</span>
                </div>
              )}
              
              <div className="flex items-center md:items-baseline gap-3 md:gap-4 mt-0.5">
                <span className="text-4xl md:text-8xl font-black text-white tracking-tighter drop-shadow-sm">
                  {isDaily && currentData.minTemp !== undefined && currentData.maxTemp !== undefined ? (
                    <div className="flex items-center md:items-baseline gap-1.5 md:gap-3">
                        <span className="text-rose-400">{currentData.maxTemp.toFixed(0)}°</span>
                        <span className="text-xl md:text-5xl text-slate-500 font-black">/</span>
                        <span className="text-2xl md:text-6xl text-blue-400">{currentData.minTemp.toFixed(0)}°</span>
                    </div>
                  ) : (
                    <>
                        {currentData.temperature.toFixed(1)}<span className="text-accent-blue">°</span>
                    </>
                  )}
                </span>
                <div className="flex flex-col">
                  <span className="text-sm md:text-3xl font-bold text-slate-200 tracking-tight leading-none">
                    {weather?.label}
                  </span>
                  {!isDaily && (
                    <div className="hidden md:flex flex-col gap-1.5 mt-2">
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
                </div>
              </div>
              {!isDaily && (
                <div className="flex md:hidden items-center gap-3 mt-1 text-[10px] font-bold uppercase tracking-tight text-slate-400">
                    <div className="flex items-center gap-1">
                        <Thermometer className="w-3 h-3 text-rose-400" />
                        <span className="text-white">{currentData.feelsLike?.toFixed(0)}°</span>
                    </div>
                    {currentData.minTemp !== undefined && (
                        <div className="flex gap-2">
                            <span className="text-rose-400/80">H: <span className="text-white">{currentData.maxTemp?.toFixed(0)}°</span></span>
                            <span className="text-blue-400/80">L: <span className="text-white">{currentData.minTemp.toFixed(0)}°</span></span>
                        </div>
                    )}
                </div>
              )}
            </div>
          </div>

          {/* Section 2: Metrics Matrix */}
          <div className="flex flex-col gap-4 md:gap-8 md:flex-row xl:flex-col xl:w-auto">
            
            {/* Atmosphere Group */}
            <div className="flex flex-col gap-2 md:gap-4">
                <h3 className="text-[10px] md:text-xs font-black text-slate-500 uppercase tracking-[0.2em] ml-1">
                    {isDaily ? "Day Atmosphere" : "Live Atmosphere"}
                </h3>
                <div className="grid grid-cols-3 xl:grid-cols-3 gap-2 md:gap-3">
                  <CompactStatBox 
                      icon={<Wind className="w-3 md:w-3.5 h-3 md:h-3.5 text-emerald-400" />}
                      label="Wind"
                      value={`${(currentData.windSpeed! * 3.6).toFixed(0)}`}
                      unit="km/h"
                      subValue={currentData.gusts != null && currentData.gusts > (currentData.windSpeed || 0) ? `G:${(currentData.gusts * 3.6).toFixed(0)}` : undefined}
                      extra={
                          <div className="flex items-center gap-1 mt-0.5 md:mt-1">
                              <Navigation2 
                                  className="w-2.5 md:w-3 h-2.5 md:h-3 text-emerald-400"
                                  style={{ transform: `rotate(${(currentData.windDir || 0) + 180}deg)`, fill: 'currentColor' }}
                              />
                              <span className="text-[9px] md:text-[10px] font-bold text-emerald-400/60 lowercase tracking-tighter">
                                  {((currentData.windDir || 0))}°
                              </span>
                          </div>
                      }
                  />
                  <CompactStatBox 
                      icon={<Sun className="w-3 md:w-3.5 h-3 md:h-3.5 text-yellow-400" />}
                      label="UV Index"
                      value={currentData.uvIndex?.toFixed(1) || "0.0"}
                      unit={currentData.uvIndex != null && currentData.uvIndex > 5 ? "High" : "Low"}
                  />
                  <CompactStatBox 
                      icon={<Eye className="w-3 md:w-3.5 h-3 md:h-3.5 text-violet-400" />}
                      label="Visibility"
                      value={currentData.visibility != null ? (currentData.visibility / 1000).toFixed(1) : "--"}
                      unit="km"
                  />
                </div>
            </div>

            {/* Snow Accumulation Group */}
            <div className="flex flex-col gap-2 md:gap-4">
                <h3 className="text-[10px] md:text-xs font-black text-slate-500 uppercase tracking-[0.2em] ml-1">
                    {isDaily ? "Snow History" : "Snow Accumulation"}
                </h3>
                <div className="grid grid-cols-3 xl:grid-cols-3 gap-2 md:gap-3">
                    <CompactStatBox 
                        icon={<Snowflake className="w-3 md:w-3.5 h-3 md:h-3.5 text-accent-cyan" />}
                        label={isDaily ? "Daily Snow" : "Rate"}
                        value={currentData.snowfall.toFixed(1)}
                        unit={isDaily ? "cm" : "cm/h"}
                    />
                    {rollingStats && (
                        <>
                            <CompactStatBox 
                                icon={<Waves className="w-3 md:w-3.5 h-3 md:h-3.5 text-blue-400" />}
                                label={isDaily ? "Prior 24h" : "24h Snow"}
                                value={rollingStats.snow24h.toFixed(1)}
                                unit="cm"
                                subValue={rollingStats.slr24h != null ? `${rollingStats.slr24h.toFixed(0)}:1` : undefined}
                            />
                            <CompactStatBox 
                                icon={<CloudRain className="w-3 md:w-3.5 h-3 md:h-3.5 text-indigo-400" />}
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
    <div className="glass-card p-2 md:p-4 rounded-xl md:rounded-2xl flex flex-col border border-white/5 bg-white/[0.02] hover:bg-white/[0.05] transition-all duration-300 min-w-0">
      <div className="flex items-center gap-1.5 md:gap-2 mb-1 md:mb-2">
        <div className="p-0.5 md:p-1 rounded-lg bg-white/5 shrink-0">{icon}</div>
        <span className="text-[9px] md:text-[11px] font-black text-slate-500 uppercase tracking-widest truncate">{label}</span>
      </div>
      <div className="flex items-baseline gap-0.5 md:gap-1">
        <span className="text-lg md:text-2xl font-black text-white tracking-tight">{value}</span>
        {unit && <span className="text-[10px] md:text-xs font-bold text-slate-500">{unit}</span>}
      </div>
      {subValue && (
        <span className="text-[9px] md:text-[11px] font-black text-accent-cyan/60 uppercase mt-0.5 md:mt-1 tabular-nums leading-none">{subValue}</span>
      )}
      {extra}
    </div>
  );
}
