import * as React from 'react';
import {Slot} from '@radix-ui/react-slot';
import {cva, type VariantProps} from 'class-variance-authority';
import {clsx} from 'clsx';
import {twMerge} from 'tailwind-merge';
const variants=cva('inline-flex items-center justify-center gap-2 rounded-lg text-[13px] font-medium whitespace-nowrap transition-colors disabled:opacity-50 disabled:cursor-not-allowed focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-300',{variants:{variant:{default:'bg-[#25324a] text-white shadow-sm hover:bg-[#1b2538]',outline:'border border-[#e4e7ec] bg-white text-[#101828] shadow-[0_1px_2px_#1018280d] hover:bg-[#f9fafb]',ghost:'text-[#475467] hover:bg-[#f2f4f7]'},size:{default:'h-9 px-3.5',sm:'h-8 px-3 text-[12.5px]'}},defaultVariants:{variant:'default',size:'default'}});
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>,VariantProps<typeof variants>{asChild?:boolean}
export const Button=React.forwardRef<HTMLButtonElement,ButtonProps>(({className,variant,size,asChild=false,...props},ref)=>{const Comp=asChild?Slot:'button';return <Comp className={twMerge(clsx(variants({variant,size,className})))} ref={ref} {...props}/>});
Button.displayName='Button';
