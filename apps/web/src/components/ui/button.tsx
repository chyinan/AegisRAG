import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-md)] text-sm font-medium transition-[background,color,box-shadow,transform] duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus-ring)] focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary:
          "bg-[#111418] text-white shadow-[0_10px_22px_rgb(17_20_24_/_0.12)] hover:bg-[#22272f] [&_*]:text-white",
        secondary:
          "bg-[var(--panel)] text-[var(--ink-primary)] shadow-[inset_0_0_0_1px_var(--line-soft)] hover:bg-[#f2f5f9]",
        ghost: "bg-transparent text-[var(--ink-secondary)] hover:bg-[#f2f5f9]",
        icon: "bg-transparent text-[var(--ink-secondary)] hover:bg-[#f2f5f9]"
      },
      size: {
        default: "min-h-9 px-3 py-1.5",
        sm: "min-h-8 px-2.5 py-1",
        icon: "size-9 p-0"
      }
    },
    defaultVariants: {
      variant: "secondary",
      size: "default"
    }
  }
);

export type ButtonProps = React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & {
    asChild?: boolean;
  };

function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : "button";
  return <Comp data-slot="button" className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}

export { Button, buttonVariants };
