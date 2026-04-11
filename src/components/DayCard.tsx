"use client";

import React from "react";
import { Snowflake, Wind, Thermometer, Cloud } from "lucide-react";
import { cn } from "@/lib/utils_tailwind";
import { getSlrColor } from "@/lib/utils";

export default function DayCard({ day, isSelected, onClick }: DayCardProps) {
  const isStorm = day.totalSnowfall >= 15;
  const avgSlr = day.totalPrecipitation > 0 ? (day.totalSnowfall * 10) / day.totalPrecipitation : 0;
  const isPowder = avgSlr >= 15 && day.totalSnowfall > 2;

  const dynamicColor = getSlrColor(avgSlr);
  
  // Calculate a "vibe color" based on total snowfall
  const getVibeColor = (snow: number) => {
    if (snow === 0) return "bg-slate-500/10 border-slate-500/20 text-slate-400";
    if (snow < 10) return "bg-blue-500/10 border-blue-500/30 text-blue-400";
    if (snow < 25) return "bg-accent-cyan/10 border-accent-cyan/40 text-accent-cyan";
    return "bg-accent-violet/10 border-accent-violet/50 text-accent-violet";
  };

  const vibeClass = getVibeColor(day.totalSnowfall);

  return (
    <div
      onClick={onClick}
      style={{
        borderColor: (isStorm || isPowder) ? dynamicColor : undefined,
        boxShadow: (isSelected && (isStorm || isPowder)) ? `0 0 30px ${dynamicColor}22` : undefined,
      } as React.CSSProperties}
      className={cn(
        "relative flex flex-col gap-4 p-4 rounded-2xl cursor-pointer transition-all duration-500 min-w-[150px] md:min-w-[180px]",
        isSelected 
          ? "glass-panel scale-105 border-white/20 z-10" 
          : "glass-card hover:translate-y-[-4px]",
        (isStorm || isPowder) && !isSelected && "border-opacity-50"
      )}
    >
      {(isStorm || isPowder) && (
        <div 
          className="absolute top-2 right-2 w-2 h-2 rounded-full animate-pulse" 
          style={{ backgroundColor: dynamicColor, boxShadow: `0 0 10px ${dynamicColor}` }} 
        />
      )}
      {/* Date Header */}
      <div className="flex flex-col gap-0.5">
        <span className="text-[10px] uppercase font-black tracking-widest text-slate-500">
          {new Intl.DateTimeFormat('en-US', { weekday: 'short' }).format(new Date(day.dateStr))}
        </span>
        <span className="text-sm font-bold text-white whitespace-nowrap">
          {new Intl.DateTimeFormat('en-US', { day: 'numeric', month: 'short' }).format(new Date(day.dateStr))}
        </span>
      </div>

      {/* Snow Value */}
      <div className="flex items-baseline gap-1.5">
        <span className={cn(
          "text-4xl font-black tracking-tighter",
          day.totalSnowfall > 0 ? "text-white" : "text-slate-700"
        )}>
          {day.totalSnowfall.toFixed(0)}
        </span>
        <span className="text-sm font-bold text-slate-500">cm</span>
      </div>

      {/* Indicators / Badges */}
      <div className="flex flex-wrap gap-1.5 mt-auto">
        {isStorm && (
          <span className="text-[9px] px-2 py-0.5 rounded-full bg-accent-rose text-white font-black uppercase tracking-tighter animate-pulse">
            Storm
          </span>
        )}
        {day.totalSnowfall > 0 && !isStorm && (
          <span className="text-[9px] px-2 py-0.5 rounded-full bg-accent-blue/30 text-accent-cyan font-black uppercase tracking-tighter">
            Snow
          </span>
        )}
      </div>

      {/* Selection Glow */}
      {isSelected && (
        <div className="absolute inset-0 rounded-2xl bg-accent-cyan/5 -z-10 animate-pulse-soft" />
      )}
    </div>
  );
}
