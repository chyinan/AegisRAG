import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex min-h-7 w-fit max-w-full items-center gap-1.5 justify-self-start self-start rounded-full px-2.5 py-1 text-xs font-semibold [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        neutral: "bg-[#f1f4f8] text-[var(--ink-secondary)]",
        source: "bg-[var(--source-soft)] text-[var(--source)]",
        index: "bg-[var(--index-soft)] text-[var(--index)]",
        danger: "bg-[var(--danger-soft)] text-[var(--danger)]",
        scope:
          "bg-white/70 text-[var(--ink-secondary)] shadow-[inset_0_0_0_1px_rgb(230_233_238_/_0.82)]"
      }
    },
    defaultVariants: {
      variant: "neutral"
    }
  }
);

export type BadgeProps = React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>;

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant, className }))} {...props} />;
}

export { Badge, badgeVariants };
