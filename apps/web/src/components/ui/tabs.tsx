import * as React from "react";
import { cn } from "@/lib/utils";

function TabsList({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="tabs-list"
      className={cn("grid grid-cols-2 gap-1 rounded-lg bg-[#edf1f6] p-1", className)}
      {...props}
    />
  );
}

function TabsTrigger({ className, ...props }: React.ComponentProps<"button">) {
  return (
    <button
      data-slot="tabs-trigger"
      className={cn(
        "min-h-8 rounded-md border-0 bg-transparent px-3 text-[var(--ink-secondary)] transition-colors aria-selected:bg-white aria-selected:text-[var(--ink-primary)] aria-selected:shadow-[0_1px_3px_rgb(20_23_31_/_0.08)]",
        className
      )}
      {...props}
    />
  );
}

export { TabsList, TabsTrigger };
