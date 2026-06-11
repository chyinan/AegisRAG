import * as React from "react";
import { cn } from "@/lib/utils";

function Select({ className, ...props }: React.ComponentProps<"select">) {
  return (
    <select
      data-slot="select"
      className={cn(
        "min-h-9 w-full rounded-md border border-transparent bg-[var(--panel-raised)] px-2.5 py-2 text-[var(--ink-primary)] shadow-[inset_0_0_0_1px_var(--line-soft)] outline-none transition-shadow focus:shadow-[inset_0_0_0_1px_rgb(37_99_235_/_0.66),0_0_0_3px_rgb(122_167_255_/_0.16)] disabled:cursor-not-allowed disabled:opacity-60",
        className
      )}
      {...props}
    />
  );
}

export { Select };
