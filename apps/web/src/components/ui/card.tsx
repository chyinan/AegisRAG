import * as React from "react";
import { cn } from "@/lib/utils";

type CardTag = "div" | "section" | "article";

type CardProps = React.ComponentProps<"div"> & {
  as?: CardTag;
};

function Card({ as: Comp = "div", className, ...props }: CardProps) {
  return (
    <Comp
      data-slot="card"
      className={cn("grid min-w-0 gap-2.5 rounded-lg bg-white/95 p-4 shadow-[var(--shadow-soft)]", className)}
      {...props}
    />
  );
}

function CardInset({ as: Comp = "div", className, ...props }: CardProps) {
  return (
    <Comp
      data-slot="card-inset"
      className={cn(
        "grid min-w-0 gap-2.5 rounded-lg bg-[var(--panel-raised)] p-3 shadow-[inset_0_0_0_1px_rgb(230_233_238_/_0.78)]",
        className
      )}
      {...props}
    />
  );
}

function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="card-header"
      className={cn("mb-3 flex flex-wrap items-center justify-between gap-2.5", className)}
      {...props}
    />
  );
}

export { Card, CardHeader, CardInset };
