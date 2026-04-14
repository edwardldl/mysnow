"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import Header from "@/components/Header";
import DateRibbon from "@/components/DateRibbon";
import { cn } from "@/lib/utils_tailwind";
import ForecastDashboard from "@/components/ForecastDashboard";
import HistorySection from "@/components/HistorySection";
import ModeToggle from "@/components/ModeToggle";
import CreditsFooter from "@/components/CreditsFooter";
import CurrentWeatherCard from "@/components/CurrentWeatherCard";
import ErrorBanner from "@/components/ErrorBanner";
import { fetchWeatherData, hasValidCache, getLocations, saveLocation, removeLocation, getLastLocationId, setLastLocationId } from "@/lib/api";
import { blendForecasts, groupData, calculateRollingStats } from "@/lib/data";
import { Location, DayData, RollingStats, WeatherDataStatus } from "@/lib/types";
import { motion, AnimatePresence } from "framer-motion";

export default function Home() {
  const [mode, setMode] = useState<"forecast" | "history">("forecast");
  const [locations, setLocations] = useState<Record<string, Location>>({});
  const [currentLocationId, setCurrentLocationId] = useState("palisades");
  const [modelId, setModelId] = useState("best_match");
  const [algoId, setAlgoId] = useState("hybrid");
  const [headerHeight, setHeaderHeight] = useState(120); // Default fallback
  const stickyHeaderRef = useRef<HTMLDivElement>(null);
  const lastFetchKeyRef = useRef<string | null>(null);

  const [forecastDays, setForecastDays] = useState<DayData[]>([]);
  const [historyDays, setHistoryDays] = useState<DayData[]>([]);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [historySelectedDate, setHistorySelectedDate] = useState<string | null>(null);
  const selectedDateRef = useRef<string | null>(null);
  const historySelectedDateRef = useRef<string | null>(null);

  // Sync refs with state
  useEffect(() => {
    selectedDateRef.current = selectedDate;
  }, [selectedDate]);

  useEffect(() => {
    historySelectedDateRef.current = historySelectedDate;
  }, [historySelectedDate]);

  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isOffline, setIsOffline] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [dataStatus, setDataStatus] = useState<WeatherDataStatus>("fresh");


  // Initialize locations and current location from persistence
  useEffect(() => {
    setLocations(getLocations());
    const lastLocId = getLastLocationId();
    if (lastLocId && getLocations()[lastLocId]) {
      setCurrentLocationId(lastLocId);
    }
  }, []);

  // Persist location selection
  useEffect(() => {
    if (currentLocationId) {
      setLastLocationId(currentLocationId);
    }
  }, [currentLocationId]);

  // Sync offline status
  useEffect(() => {
    const handleOnline = () => setIsOffline(false);
    const handleOffline = () => setIsOffline(true);
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
    };
  }, []);

  const loadData = useCallback(async (locId: string, model: string, algo: string, force = false) => {
    const cached = !force && hasValidCache(locId, model);
    
    // Only show the full-screen loader if we don't have cached data
    if (!cached) {
      setIsLoading(true);
    }
    
    setError(null);
    setIsOffline(!navigator.onLine);
    setRefreshKey(prev => prev + 1);
    try {
      const data = await fetchWeatherData(locId, model, force);
      const blended = blendForecasts(data.hrrrData, data.ecmwfData, data.location, algo, data.mode);
      const grouped = groupData(blended, data.location.timezone);
      setForecastDays(grouped);
      setDataStatus(data.status);
      setLocations(getLocations()); // Refresh locations to pick up backfilled metadata (elevation, timezone)
      setIsOffline(false);

      // Ensure the initial selection preserves the previous date if available,
      // otherwise fallback to "today", and if that's not available, the first day.
      if (grouped.length > 0) {
        const timezone = data.location.timezone || 'America/Los_Angeles';
        const todayStr = new Date().toLocaleDateString('en-CA', { timeZone: timezone });
        const prevDate = selectedDateRef.current;
        const prevDateExists = grouped.find(d => d.dateStr === prevDate);

        if (prevDate && prevDateExists) {
          setSelectedDate(prevDate);
        } else {
          const todayExists = grouped.find(d => d.dateStr === todayStr);
          setSelectedDate(todayExists ? todayStr : grouped[0].dateStr);
        }
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to fetch weather data");
      setIsOffline(!navigator.onLine);
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const fetchKey = `${currentLocationId}-${modelId}-${algoId}`;
    if (Object.keys(locations).length > 0 && lastFetchKeyRef.current !== fetchKey) {
      lastFetchKeyRef.current = fetchKey;
      loadData(currentLocationId, modelId, algoId);
    }
  }, [currentLocationId, modelId, algoId, locations, loadData]);

  // Prefetch data for all other locations when model changes or locations list is updated.
  // This ensures that switching locations is instantaneous.
  useEffect(() => {
    if (Object.keys(locations).length <= 1) return;

    const prefetch = async () => {
      // Small delay to let the initial primary fetch settle
      await new Promise(resolve => setTimeout(resolve, 1000));

      const locIds = Object.keys(locations);
      for (const locId of locIds) {
        // Skip current location because fetchWeatherData was already called for it by loadData
        if (locId === currentLocationId) continue;

        try {
          // Fetch sequentially to avoid race conditions when updating location metadata in localStorage
          await fetchWeatherData(locId, modelId);
          // Small delay between locations to avoid hitting rate limits (429)
          await new Promise(resolve => setTimeout(resolve, 500));
        } catch (err) {
          console.warn(`Prefetch failed for ${locId} with model ${modelId}:`, err);
        }
      }
    };

    prefetch();
  }, [modelId, locations, currentLocationId]);

  // Dynamic header height measurement
  useEffect(() => {
    if (!stickyHeaderRef.current) return;

    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        // Height of the sticky container + height of the main fixed header (60 or 72px)
        const isMobile = window.innerWidth < 768;
        const mainHeaderHeight = isMobile ? 60 : 72;
        setHeaderHeight(entry.contentRect.height + mainHeaderHeight);
      }
    });

    observer.observe(stickyHeaderRef.current);
    return () => observer.disconnect();
  }, []);

  const handleAddLocation = (name: string, lat: string, lon: string) => {
    const id = name.toLowerCase().replace(/\s+/g, '-');
    const newLocs = saveLocation(id, name, lat, lon);
    setLocations(newLocs);
    setCurrentLocationId(id);
  };

  const handleRemoveLocation = (id: string) => {
    const newLocs = removeLocation(id);
    setLocations(newLocs);
    if (currentLocationId === id) {
      setCurrentLocationId("palisades");
    }
  };

  const timeInfo = React.useMemo(() => {
    const now = new Date();
    const fmt = new Intl.DateTimeFormat('en-US', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      hourCycle: 'h23',
      timeZone: locations[currentLocationId]?.timezone || 'America/Los_Angeles'
    });

    const parts = fmt.formatToParts(now);
    const y = parts.find(p => p.type === 'year')?.value;
    const m = parts.find(p => p.type === 'month')?.value;
    const d = parts.find(p => p.type === 'day')?.value;
    const h = parts.find(p => p.type === 'hour')?.value;

    return {
      currentHourISO: `${y}-${m}-${d}T${h}:00`,
      todayStr: `${y}-${m}-${d}`
    };
  }, [locations, currentLocationId]);

  const { currentHourISO, todayStr } = timeInfo;

  // Find current hour data for the header
  const currentHourData = React.useMemo(() => {
    if (forecastDays.length === 0) return null;

    // Search across all days for the current hour
    let found = null;
    for (const day of forecastDays) {
      found = day.hourly.find(pt => pt.time.startsWith(currentHourISO));
      if (found) break;
    }

    // Fallback: If not found, try to find the start of today, otherwise just the first day's first hour.
    if (!found) {
      const todayDay = forecastDays.find(d => d.dateStr === todayStr);
      found = todayDay ? todayDay.hourly[0] : forecastDays[0].hourly[0];
    }

    return found;
  }, [forecastDays, currentHourISO, todayStr]);

  const activeDays = mode === "forecast" ? forecastDays : historyDays;
  const activeSelectedDate = mode === "forecast" ? selectedDate : historySelectedDate;
  const setActiveSelectedDate = mode === "forecast" ? setSelectedDate : setHistorySelectedDate;

  const displayData = React.useMemo(() => {
    if (activeDays.length === 0) return null;

    const isTodaySelected = activeSelectedDate === todayStr;
    const selectedDay = activeDays.find(d => d.dateStr === activeSelectedDate);

    if (isTodaySelected && currentHourData && mode === "forecast") {
      return {
        data: {
          ...currentHourData,
          minTemp: selectedDay?.minTemp,
          maxTemp: selectedDay?.maxTemp
        },
        isDaily: false
      };
    }

    if (selectedDay) {
      // Create a summary "BlendedHour" from DayData
      const summary = {
        time: selectedDay.dateStr + "T12:00:00",
        temperature: selectedDay.maxTemp,
        minTemp: selectedDay.minTemp,
        maxTemp: selectedDay.maxTemp,
        feelsLike: selectedDay.hourly[12]?.feelsLike || selectedDay.maxTemp,
        precipProb: Math.max(...selectedDay.hourly.map(h => h.precipChance || 0)),
        liquidMM: selectedDay.totalPrecipitation,
        snowfall: selectedDay.totalSnowfall,
        windSpeed: selectedDay.hourly.reduce((acc, h) => acc + (h.windSpeed || 0), 0) / (selectedDay.hourly.length || 1),
        windDir: selectedDay.hourly[12]?.windDir,
        gusts: Math.max(...selectedDay.hourly.map(h => h.gusts || 0)),
        uvIndex: Math.max(...selectedDay.hourly.map(h => h.uvIndex || 0)),
        visibility: Math.max(...selectedDay.hourly.map(h => h.visibility || 0)),
        weatherCode: selectedDay.weatherCode || 0,
      };
      return { data: summary as any, isDaily: true };
    }

    return null;
  }, [activeDays, activeSelectedDate, todayStr, currentHourData, mode]);

  const rollingStats = React.useMemo(() => {
    if (activeDays.length === 0 || !displayData) return null;
    const allHourly = activeDays.flatMap(d => d.hourly);

    // Logic: If Today, use current hour. 
    // If not Today, use start of the selected Day (to look back at preceding 24h/48h).
    const isTodaySelected = activeSelectedDate === todayStr;
    const refTime = (isTodaySelected && mode === "forecast") ? currentHourData?.time : activeSelectedDate + "T00:00:00";

    if (!refTime) return null;
    return calculateRollingStats(allHourly, refTime);
  }, [activeDays, displayData, currentHourData, activeSelectedDate, todayStr, mode]);

  return (
    <div className="min-h-screen bg-background flex flex-col font-sans">
      <Header
        onRefresh={() => loadData(currentLocationId, modelId, algoId, true)}
        isLoading={isLoading}
        isOffline={isOffline}
        currentData={currentHourData}
        locations={locations}
        currentLocationId={currentLocationId}
        onSelectLocation={setCurrentLocationId}
        onAddLocation={handleAddLocation}
        onRemoveLocation={handleRemoveLocation}
        modelId={modelId}
        setModelId={setModelId}
        algoId={algoId}
        setAlgoId={setAlgoId}
      />

      <div
        ref={stickyHeaderRef}
        className="fixed top-[calc(60px+var(--sat,0px))] md:top-[calc(72px+var(--sat,0px))] left-0 right-0 z-40 transition-all duration-300 bg-slate-950/80 backdrop-blur-xl border-b border-white/5"
      >
        <DateRibbon
          days={activeDays}
          selectedDate={activeSelectedDate}
          onSelect={setActiveSelectedDate}
          timezone={locations[currentLocationId]?.timezone}
        />
      </div>

      <main
        className="flex-1 overflow-y-auto no-scrollbar scroll-smooth text-slate-50"
        style={{ paddingTop: `calc(${headerHeight}px + var(--sat, 0px))` }}
      >
        <AnimatePresence mode="wait">
          <div className="flex flex-col">
            {mode === "history" && (
              <HistorySection
                currentLocationId={currentLocationId}
                onResults={(days) => {
                  setHistoryDays(days);
                  if (days.length > 0) {
                    const prevDate = historySelectedDateRef.current;
                    const prevDateExists = days.find(d => d.dateStr === prevDate);
                    setHistorySelectedDate(prevDate && prevDateExists ? prevDate : days[0].dateStr);
                  }
                }}
              />
            )}

            {error && activeDays.length > 0 && (
              <ErrorBanner
                message={error}
                isOffline={isOffline}
                onRetry={() => loadData(currentLocationId, modelId, algoId)}
              />
            )}

            <motion.div
              key={`${activeSelectedDate || mode}-${refreshKey}`}
              initial={{ opacity: 0, scale: 0.98 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 1.02 }}
              transition={{ duration: 0.5, ease: "easeOut" }}
            >
              <CurrentWeatherCard
                currentData={displayData?.data || null}
                isDaily={displayData?.isDaily || false}
                rollingStats={rollingStats}
                locationName={locations[currentLocationId]?.name || "Palisades Tahoe"}
                timezone={locations[currentLocationId]?.timezone}
                dataStatus={dataStatus}
                className={cn(
                  "mt-4 md:mt-4",
                  (error && activeDays.length > 0) && "mt-2 md:mt-2"
                )}
              />

              {error && activeDays.length === 0 ? (
                <div className="max-w-7xl mx-auto px-4 md:px-8 mt-12 pb-20">
                  <div className="glass-panel p-8 rounded-3xl border-accent-rose/20 text-center">
                    <h3 className="text-xl font-bold text-accent-rose mb-2">Error Loading Data</h3>
                    <p className="text-slate-400">{error}</p>
                    <motion.button
                      whileHover={{ scale: 1.05 }}
                      whileTap={{ scale: 0.95 }}
                      onClick={() => loadData(currentLocationId, modelId, algoId)}
                      className="mt-6 px-6 py-2 bg-accent-rose text-white rounded-full font-bold text-sm shadow-lg shadow-rose-500/20"
                    >
                      Retry
                    </motion.button>
                  </div>
                </div>
              ) : (
                <ForecastDashboard
                  days={activeDays}
                  isLoading={isLoading && mode === "forecast"}
                  selectedDate={activeSelectedDate}
                  timezone={locations[currentLocationId]?.timezone}
                  dataStatus={dataStatus}
                />
              )}
            </motion.div>
          </div>
        </AnimatePresence>

        <CreditsFooter mode={mode} setMode={setMode} />
      </main>

      {/* Decorative Background Elements */}
      <div className="fixed top-0 left-0 w-full h-full -z-50 overflow-hidden pointer-events-none">
        <div className="absolute top-[-10%] right-[-10%] w-[40%] h-[40%] bg-accent-blue/5 rounded-full blur-[120px] animate-pulse-soft" />
        <div className="absolute bottom-[-10%] left-[-10%] w-[40%] h-[40%] bg-accent-violet/5 rounded-full blur-[120px] animate-pulse-soft " style={{ animationDelay: '1.5s' }} />
      </div>
    </div>
  );
}
