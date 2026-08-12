/* eslint-disable i18next/no-literal-string */
import React from "react";
import {
  TrendDownIcon,
  TrendUpIcon,
} from "#/components/shared/icons/inline-icons";
import { formatCost, formatShortDate } from "./usage-dashboard-utils";

export function KPICard({
  label,
  value,
  trend,
  trendUp,
}: {
  label: string;
  value: string | number;
  trend?: string;
  trendUp?: boolean;
}) {
  return (
    <div className="bg-base-secondary border border-border-subtle rounded-lg px-4 py-3">
      <span className="text-sm font-medium leading-5 text-muted">{label}</span>
      <div className="mt-1 text-2xl font-bold text-foreground">{value}</div>
      {trend && (
        <div
          className={`flex items-center gap-1 mt-2 text-xs ${trendUp ? "text-green-400" : "text-red-400"}`}
        >
          {trendUp ? <TrendUpIcon /> : <TrendDownIcon />}
          {trend}
        </div>
      )}
    </div>
  );
}

export function AreaChart({
  data,
}: {
  data: { date: string; value: number }[];
}) {
  const maxValue = Math.max(...data.map((d) => d.value), 1);
  const minValue = Math.min(...data.map((d) => d.value), 0);
  const range = maxValue - minValue || 1;

  const width = 100;
  const height = 100;
  const points = data.map((d, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((d.value - minValue) / range) * height;
    return `${x},${y}`;
  });

  const pathD = `M ${points.join(" L ")}`;
  const areaD = `${pathD} L ${width},${height} L 0,${height} Z`;

  return (
    <div className="relative h-48 w-full">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-full"
        preserveAspectRatio="none"
      >
        {[0, 25, 50, 75, 100].map((pct) => (
          <line
            key={pct}
            x1="0"
            y1={`${pct}%`}
            x2="100%"
            y2={`${pct}%`}
            stroke="var(--oh-border-subtle)"
            strokeWidth="0.5"
          />
        ))}
        <path d={areaD} fill="url(#usageAreaGradient)" opacity="0.3" />
        <path
          d={pathD}
          fill="none"
          stroke="var(--oh-color-primary)"
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
        />
        <defs>
          <linearGradient
            id="usageAreaGradient"
            x1="0%"
            y1="0%"
            x2="0%"
            y2="100%"
          >
            <stop
              offset="0%"
              stopColor="var(--oh-color-primary)"
              stopOpacity="0.5"
            />
            <stop
              offset="100%"
              stopColor="var(--oh-color-primary)"
              stopOpacity="0"
            />
          </linearGradient>
        </defs>
      </svg>
      <div className="absolute left-0 top-0 bottom-0 flex flex-col justify-between text-xs text-text-dim -ml-2">
        <span>{maxValue.toLocaleString()}</span>
        <span>{Math.round((maxValue + minValue) / 2).toLocaleString()}</span>
        <span>{minValue.toLocaleString()}</span>
      </div>
      <div className="absolute bottom-0 left-0 right-0 flex justify-between text-xs text-text-dim mt-2">
        {data
          .filter((_, i) => i % Math.ceil(data.length / 7) === 0)
          .map((d) => (
            <span key={d.date}>{formatShortDate(d.date)}</span>
          ))}
      </div>
    </div>
  );
}

export function PieChart({
  data,
  total,
}: {
  data: { value: number; color: string; label: string; percent: number }[];
  total: number;
}) {
  const size = 112;
  const strokeWidth = 12;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = React.useState<{
    label: string;
    value: number;
    percent: number;
    x: number;
    y: number;
  } | null>(null);
  let offset = 0;

  const updateHoverPosition = (
    event: React.MouseEvent<SVGCircleElement>,
    segment: { label: string; value: number; percent: number },
  ) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) {
      return;
    }
    setHovered({
      label: segment.label,
      value: segment.value,
      percent: segment.percent,
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    });
  };

  return (
    <div ref={containerRef} className="relative h-28 w-28">
      <svg viewBox={`0 0 ${size} ${size}`} className="h-full w-full">
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="transparent"
            stroke="var(--oh-border-subtle)"
            strokeWidth={strokeWidth}
            className="pointer-events-none"
          />
          {data.map((segment, index) => {
            const portion = total > 0 ? segment.value / total : 0;
            const segmentLength = circumference * portion;
            const dashArray = `${segmentLength} ${circumference - segmentLength}`;
            const segmentOffset = offset;
            offset += segmentLength;
            return (
              <circle
                key={`${segment.color}-${index}`}
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="transparent"
                stroke={segment.color}
                strokeWidth={strokeWidth}
                strokeDasharray={dashArray}
                strokeDashoffset={-segmentOffset}
                strokeLinecap="round"
                className="cursor-pointer transition-[stroke-width] hover:stroke-[14]"
                onMouseEnter={(event) => updateHoverPosition(event, segment)}
                onMouseMove={(event) => updateHoverPosition(event, segment)}
                onMouseLeave={() => setHovered(null)}
              />
            );
          })}
        </g>
      </svg>
      {hovered && (
        <div
          className="pointer-events-none absolute z-10 min-w-32 -translate-x-1/2 -translate-y-[calc(100%+8px)] rounded-lg border border-border-subtle bg-base-secondary px-3 py-2 shadow-lg"
          style={{ left: hovered.x, top: hovered.y }}
        >
          <div className="text-sm font-medium text-foreground">
            {hovered.label}
          </div>
          <div className="mt-0.5 text-xs tabular-nums text-muted">
            {formatCost(hovered.value)} · {hovered.percent.toFixed(1)}%
          </div>
        </div>
      )}
    </div>
  );
}
