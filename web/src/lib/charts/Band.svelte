<script lang="ts">
  // The min/max envelope for one metric — a faint filled area between the
  // per-bucket low and high (raw spread), drawn behind its line so smoothing the
  // line doesn't hide the actual excursions. Same y-scale as the line.
  import { scaleLinear, type ScaleTime } from 'd3-scale'
  import { area } from 'd3-shape'
  import { getContext } from 'svelte'
  import type { Readable } from 'svelte/store'

  interface LC {
    height: Readable<number>
    xScale: Readable<ScaleTime<number, number>>
  }
  const { height, xScale } = getContext<LC>('LayerCake')

  interface Span {
    t: number
    lo: number | null
    hi: number | null
  }
  let {
    points,
    domain,
    color,
  }: { points: Span[]; domain: [number, number]; color: string } = $props()

  const path = $derived.by(() => {
    const y = scaleLinear(domain, [$height, 0])
    const gen = area<Span>()
      .defined((p) => p.lo != null && p.hi != null)
      .x((p) => $xScale(new Date(p.t)))
      .y0((p) => y(p.lo as number))
      .y1((p) => y(p.hi as number))
    return gen(points) ?? ''
  })
</script>

<path d={path} fill={color} fill-opacity="0.16" stroke="none" />
