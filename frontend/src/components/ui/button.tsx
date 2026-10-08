import * as React from 'react';
import {Slot} from '@radix-ui/react-slot';
import {cva, type VariantProps} from 'class-variance-authority';
import {clsx} from 'clsx';
import {twMerge} from 'tailwind-merge';
const variants=cva('inline-flex items-center justify-center gap-2 rounded-lg text-sm font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400',{variants:{variant:{default:'bg-navy text-white hover:bg-slate-700',outline:'border border-slate-200 bg-white text-slate-700 hover:bg-slate-50',ghost:'text-slate-600 hover:bg-slate-100'},size:{default:'h-10 px-4 py-2',sm:'h-8 px-3 text-xs'}},defaultVariants:{variant:'default',size:'default'}});
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>,VariantProps<typeof variants>{asChild?:boolean}
export const Button=React.forwardRef<HTMLButtonElement,ButtonProps>(({className,variant,size,asChild=false,...props},ref)=>{const Comp=asChild?Slot:'button';return <Comp className={twMerge(clsx(variants({variant,size,className})))} ref={ref} {...props}/>});
Button.displayName='Button';
