interface Props { grade: string }

const CLASS_MAP: Record<string, string> = {
  'A+':     'grade-a-plus',
  'A':      'grade-a',
  'B+':     'grade-b-plus',
  'B':      'grade-b',
  'C':      'grade-c',
  'REJECT': 'grade-reject',
}

export default function GradeBadge({ grade }: Props) {
  const cls = CLASS_MAP[grade] ?? 'grade-reject'
  return <span className={`grade-badge ${cls}`}>{grade}</span>
}
