export function canonicalReelUrl(value: string): string | null {
  try {
    const url = new URL(value.trim());
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.port || /[\s<>\\"']/.test(value)) return null;
    const host = url.hostname.toLowerCase();
    const path = url.pathname.replace(/\/+$/, '');
    if (['instagram.com', 'www.instagram.com', 'm.instagram.com', 'instagr.am', 'www.instagr.am'].includes(host)) {
      const match = path.match(/^\/(reel|reels|p|tv)\/([A-Za-z0-9_-]+)$/);
      if (match) return `https://www.instagram.com/reel/${match[2]}/`;
      if (/^\/share\/(?:(?:reel|p)\/)?[A-Za-z0-9_-]+$/.test(path)) return `https://www.instagram.com${path}/`;
      if (host.endsWith('instagr.am') && /^\/[A-Za-z0-9_/-]+$/.test(path)) return `https://${host}${path}/`;
      return null;
    }
    if (['tiktok.com', 'www.tiktok.com', 'm.tiktok.com'].includes(host)) {
      const match = path.match(/^\/@([\w.-]*)\/video\/(\d+)$/);
      if (match) return `https://www.tiktok.com/@${match[1]}/video/${match[2]}`;
      if (/^\/t\/[A-Za-z0-9]+$/.test(path)) return `https://www.tiktok.com${path}/`;
    }
    if (['vm.tiktok.com', 'vt.tiktok.com'].includes(host) && /^\/[A-Za-z0-9]+$/.test(path)) return `https://${host}${path}/`;
    let id;
    if (['youtube.com', 'www.youtube.com', 'm.youtube.com'].includes(host)) {
      id = path.match(/^\/(?:shorts|watch)\/([A-Za-z0-9_-]{11})$/)?.[1] || (path === '/watch' ? url.searchParams.get('v') : null);
    } else if (host === 'youtu.be') id = path.slice(1);
    return id && /^[A-Za-z0-9_-]{11}$/.test(id) ? `https://www.youtube.com/watch?v=${id}` : null;
  } catch { return null; }
}

export function reelUrls(text: string): string[] {
  return [...new Set((text.match(/https?:\/\/[^\s<>"')]+/gi) || [])
    .map((url) => canonicalReelUrl(url.replace(/[.,!;]+$/, ''))).filter((value): value is string => value !== null))];
}

