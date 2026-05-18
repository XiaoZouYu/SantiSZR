import { Slider } from "@/components/ui/slider"

export interface RangeSliderProps
  extends Omit<React.ComponentPropsWithoutRef<typeof Slider>, "value" | "min" | "max" | "step" | "onValueChange"> {
  value: number
  min: number
  max: number
  step?: number
  onValueChange: (value: number) => void
}

function RangeSlider({ value, min, max, step = 1, onValueChange, ...props }: RangeSliderProps) {
  return <Slider value={[value]} min={min} max={max} step={step} onValueChange={(next) => onValueChange(next[0] ?? value)} {...props} />
}

export { RangeSlider }
