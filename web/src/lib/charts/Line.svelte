<script lang="ts">
  // One metric's line. Reads the shared x (time) scale + plot height from LayerCake
  // context, builds its own linear y-scale from the passed domain (so series sharing
  // an axis share a domain), and breaks the path across null buckets (gaps).
  import { scaleLinear, type ScaleTime } from 'd3-scale'
  import { line } from 'd3-shape'
  import { getContext } from 'svelte'
  import type { Readable } from 'svelte/store'

  import { lineSegments } from './util'

  interface LC {
    height: Readable<number>
    xScale: Readable<ScaleTime<number, number>>
  }
  const { height, xScale } = getContext<LC>('LayerCake')

  interface Point {
    t: number
    v: number | null
  }
  let {
    points,
    domain,
    color,
  }: { points: Point[]; domain: [number, number]; color: string } = $props()

  // Connected runs of defined points (gap-bridged: small empty-bucket runs from a
  // fine grid stay connected; genuine no-data gaps split the line). One path per
  // run; a lone sample (singleton run) becomes a dot so it stays visible.
  const segments = $derived(
    lineSegments(
      points.map((p) => p.t),
      points.map((p) => p.v != null)
    )
  )
  const paths = $derived.by(() => {
    const y = scaleLinear(domain, [$height, 0])
    const gen = line<Point>()
      .x((p) => $xScale(new Date(p.t)))
      .y((p) => y(p.v as number))
    return segments
      .filter((s) => s.length >= 2)
      .map((s) => gen(s.map((i) => points[i])) ?? '')
  })
  const dots = $derived.by(() => {
    const y = scaleLinear(domain, [$height, 0])
    return segments
      .filter((s) => s.length === 1)
      .map((s) => ({ cx: $xScale(new Date(points[s[0]].t)), cy: y(points[s[0]].v as number) }))
  })
</script>

{#each paths as d, i (i)}
  <path {d} fill="none" stroke={color} stroke-width="1.5" stroke-linejoin="round" />
{/each}
{#each dots as d (d.cx)}
  <circle cx={d.cx} cy={d.cy} r="2" fill={color} />
{/each}
