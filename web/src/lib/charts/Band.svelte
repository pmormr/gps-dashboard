<script lang="ts">
  // The min/max envelope for one metric — a faint filled area between the
  // per-bucket low and high (raw spread), drawn behind its line so smoothing the
  // line doesn't hide the actual excursions. Same y-scale as the line.
  import { scaleLinear, type ScaleTime } from 'd3-scale'
  import { area } from 'd3-shape'
  import { getContext } from 'svelte'
  import type { Readable } from 'svelte/store'

  import { lineSegments } from './util'

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

  // Same gap-bridging as the line, so the envelope tracks it instead of shattering
  // at every empty bucket once zoomed in.
  const segments = $derived(
    lineSegments(
      points.map((p) => p.t),
      points.map((p) => p.lo != null && p.hi != null)
    )
  )
  const paths = $derived.by(() => {
    const y = scaleLinear(domain, [$height, 0])
    const gen = area<Span>()
      .x((p) => $xScale(new Date(p.t)))
      .y0((p) => y(p.lo as number))
      .y1((p) => y(p.hi as number))
    return segments
      .filter((s) => s.length >= 2)
      .map((s) => gen(s.map((i) => points[i])) ?? '')
  })
</script>

{#each paths as d, i (i)}
  <path {d} fill={color} fill-opacity="0.16" stroke="none" />
{/each}
