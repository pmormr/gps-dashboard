<script lang="ts">
  // One metric's line. Reads the shared x (time) scale + plot height from LayerCake
  // context, builds its own linear y-scale from the passed domain (so series sharing
  // an axis share a domain), and breaks the path across null buckets (gaps).
  import { scaleLinear, type ScaleTime } from 'd3-scale'
  import { line } from 'd3-shape'
  import { getContext } from 'svelte'
  import type { Readable } from 'svelte/store'

  import { isolatedIndices } from './util'

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

  const path = $derived.by(() => {
    const y = scaleLinear(domain, [$height, 0])
    const gen = line<Point>()
      .defined((p) => p.v != null)
      .x((p) => $xScale(new Date(p.t)))
      .y((p) => y(p.v as number))
    return gen(points) ?? ''
  })

  // d3's line draws only segments between consecutive defined points, so a point
  // with null neighbours (a brief, isolated burst — common in engine-gated data
  // once zoomed in) would be invisible. Mark those as dots so they still show.
  const dots = $derived.by(() => {
    const y = scaleLinear(domain, [$height, 0])
    return isolatedIndices(points.map((p) => p.v)).map((i) => ({
      cx: $xScale(new Date(points[i].t)),
      cy: y(points[i].v as number),
    }))
  })
</script>

<path d={path} fill="none" stroke={color} stroke-width="1.5" stroke-linejoin="round" />
{#each dots as d (d.cx)}
  <circle cx={d.cx} cy={d.cy} r="2" fill={color} />
{/each}
