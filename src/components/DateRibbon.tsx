"use client";

import React from "react";
import { cn } from "@/lib/utils_tailwind";
import { DayData } from "@/lib/types";
import { Snowflake } from "lucide-react";
import { formatCalendarDate, getSlrColor, getWeatherDescription } from "@/lib/utils";
import { motion } from "framer-motion";

interface DateRibbonProps {
  days: DayData[];
  selectedDate: string | null;
  onSelect: (date: string) => void;
  timezone?: string;
}

export default function DateRibbon({ days, selectedDate, onSelect, timezone = 'America/Los_Angeles' }: DateRibbonProps) {
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const todayRef = React.useRef<HTMLButtonElement>(null);
  const dividerRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    const container = scrollRef.current;
    const target = dividerRef.current || todayRef.current;

    if (container && target) {
      // Small delay to ensure layout is ready
      const timeoutId = window.setTimeout(() => {
        container.scrollTo({
          left: target.offsetLeft - container.offsetLeft,
          behavior: "smooth"
        });
      }, 100);
      return () => window.clearTimeout(timeoutId);
    }
  }, [days]); // Re-run when days change (e.g. on initial load)

  return (
    <div
      ref={scrollRef}
      className="w-full py-3 px-4 md:px-8 overflow-x-auto no-scrollbar scroll-smooth"
    >
      <div className="max-w-7xl mx-auto flex gap-3 md:gap-4 items-center">
        {days.map((day, index) => {
          const isSelected = day.dateStr === selectedDate;
          const todayStr = new Date().toLocaleDateString('en-CA', { timeZone: timezone });
          const isToday = day.dateStr === todayStr;
          const isPast = day.dateStr < todayStr;

          const dayName = isToday ? "Today" : formatCalendarDate(day.dateStr, { weekday: 'short' });
          const dayNum = day.dateStr.split('-')[2];

          // NEW: Storm & SLR Logic
          const isStorm = day.totalSnowfall >= 15;
          const frozenSweMm = day.hourly.reduce((sum, hour) => sum + (hour.frozenSweMm ?? 0), 0);
          const avgSlr = frozenSweMm > 0 ? (day.totalSnowfall * 10) / frozenSweMm : 0;
          const slrColor = getSlrColor(avgSlr);

          return (
            <React.Fragment key={day.dateStr}>
              {/* Divider before Today */}
              {isToday && index > 0 && (
                <div
                  ref={dividerRef}
                  className="h-12 w-[1px] bg-white/10 shrink-0 self-center"
                />
              )}

              <button
                ref={isToday ? todayRef : null}
                onClick={() => onSelect(day.dateStr)}
                className={cn(
                  "flex flex-col items-center min-w-[60px] md:min-w-[70px] py-2 rounded-xl transition-all duration-300 relative group overflow-hidden",
                  isSelected
                    ? "bg-accent-blue text-white shadow-lg shadow-blue-500/20"
                    : (isStorm ? "text-white" : "bg-white/5 text-slate-400 hover:bg-white/10 hover:text-slate-200"),
                  isPast && !isSelected && "opacity-75"
                )}
              >
                {/* Storm Pulsing Background */}
                {isStorm && !isSelected && (
                  <motion.div
                    animate={{
                      opacity: isPast ? [0.4, 0.6, 0.4] : [0.6, 1, 0.6],
                      scale: [0.95, 1.05, 0.95],
                    }}
                    transition={{ repeat: Infinity, duration: 3, ease: "easeInOut" }}
                    className="absolute inset-0 z-0"
                    style={{ backgroundColor: slrColor }}
                  />
                )}

                <div className="relative z-10 flex flex-col items-center w-full px-1">
                  <div className="flex items-center gap-1 mb-0.5">
                    <span className={cn(
                      "text-[9px] font-black uppercase tracking-tighter opacity-80",
                      isStorm && !isSelected && "text-white/90",
                      isPast && !isSelected && "text-slate-500 font-bold"
                    )}>
                      {dayName}
                    </span>
                    <span className={cn("text-xs", isPast && !isSelected && "opacity-70")}>{getWeatherDescription(day.weatherCode).icon}</span>
                  </div>

                  <span className={cn(
                    "text-lg font-black leading-none mb-1",
                    isStorm && !isSelected && "text-white",
                    isPast && !isSelected && "text-slate-300"
                  )}>
                    {dayNum}
                  </span>

                  <div className="flex flex-col items-center gap-0.5 mt-auto">
                    <div className="flex items-center gap-1.5 text-[9px] font-black tabular-nums">
                      <span className={cn("text-rose-400", isPast && !isSelected && "opacity-70")}>{day.maxTemp.toFixed(0)}°</span>
                      <span className={cn("text-blue-400", isPast && !isSelected && "opacity-70")}>{day.minTemp.toFixed(0)}°</span>
                    </div>

                    {day.totalSnowfall > 0 && (
                      <div className={cn(
                        "flex items-center gap-0.5 px-1 rounded-sm",
                        "bg-accent-blue/20"
                      )}>
                        <Snowflake className={cn(
                          "w-2 h-2",
                          isSelected ? "text-white" : (isStorm ? "text-white" : "text-accent-cyan"),
                          isPast && !isSelected && "opacity-70"
                        )} />
                        <span className={cn(
                          "text-[9px] font-black",
                          isStorm && !isSelected ? "text-white" : "text-accent-cyan",
                          isPast && !isSelected && "opacity-70"
                        )}>{day.totalSnowfall.toFixed(0)}</span>
                      </div>
                    )}
                  </div>
                </div>

                {isSelected && (
                  <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 bg-white rounded-full z-10" />
                )}

                {/* History Badge for past days */}
                {isPast && !isSelected && (
                  <div className="absolute top-0 right-0 w-full h-full border border-white/5 rounded-xl pointer-events-none" />
                )}
              </button>
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}
