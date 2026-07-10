import { connect } from "framer-api"
import { writeFileSync } from "fs"

const PROJECT_URL = process.env.FRAMER_PROJECT_URL
const API_KEY = process.env.FRAMER_API_KEY
const ARTICLES_COLLECTION_ID = "yY_gb2Idp"

const FIELD_TITLE = "XQ4Xt2wDy"
const FIELD_DATE = "PP1bxVtET"
const FIELD_CATEGORY = "ZzMPt6bcp"
const FIELD_AUTHOR = "MFHqJgXWD"
const FIELD_LEAD = "P08zToifZ"
const FIELD_CONTENT = "X6fF5tFLU"
const FIELD_META_DESC = "AU4xhMc8d"
const FIELD_FEATURED_IMAGE = "xWLq6JWrJ"

const AUTHOR_NAMES = { "typhoon-editorial": "Typhoon Editorial" }

function escapeXml(str) {
    return String(str ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
}

function stripHtml(html) {
    return String(html ?? "")
        .replace(/<[^>]+>/g, " ")
        .replace(/\s+/g, " ")
        .trim()
}

function toRfc822(date) {
    return date.toUTCString().replace("GMT", "+0000")
}

function fieldValue(item, fieldId) {
    return item.fieldData?.[fieldId]?.value
}

async function main() {
    if (!PROJECT_URL) throw new Error("Missing FRAMER_PROJECT_URL env var")
    if (!API_KEY) throw new Error("Missing FRAMER_API_KEY env var")

    const framer = await connect(PROJECT_URL, API_KEY)
    let items
    try {
        const collection = await framer.getCollection(ARTICLES_COLLECTION_ID)
        if (!collection) throw new Error(`Collection ${ARTICLES_COLLECTION_ID} not found`)
        items = await collection.getItems()
    } finally {
        await framer.disconnect()
    }

    const entries = items
        .filter((it) => !it.draft)
        .map((it) => {
            const dateRaw = fieldValue(it, FIELD_DATE)
            const featuredImage = fieldValue(it, FIELD_FEATURED_IMAGE)
            const authorSlug = fieldValue(it, FIELD_AUTHOR)
            const lead = fieldValue(it, FIELD_LEAD) || ""
            const content = fieldValue(it, FIELD_CONTENT) || ""
            const metaDesc = fieldValue(it, FIELD_META_DESC) || ""

            return {
                title: fieldValue(it, FIELD_TITLE) || it.slug,
                link: `https://typhoon.coffee/blog/${it.slug}`,
                date: dateRaw ? new Date(dateRaw) : null,
                category: fieldValue(it, FIELD_CATEGORY) || "",
                author: AUTHOR_NAMES[authorSlug] || "Typhoon Coffee",
                description: metaDesc || stripHtml(lead).slice(0, 300),
                contentHtml: lead + content,
                image: featuredImage?.url || null,
            }
        })
        .filter((e) => e.date)
        .sort((a, b) => b.date - a.date)

    const itemsXml = entries
        .map(
            (e) => `    <item>
      <title>${escapeXml(e.title)}</title>
      <link>${escapeXml(e.link)}</link>
      <guid isPermaLink="true">${escapeXml(e.link)}</guid>
      <pubDate>${toRfc822(e.date)}</pubDate>
      <dc:creator>${escapeXml(e.author)}</dc:creator>
      <category>${escapeXml(e.category)}</category>
      <description>${escapeXml(e.description)}</description>
      <content:encoded><![CDATA[${e.contentHtml}]]></content:encoded>${
          e.image
              ? `\n      <enclosure url="${escapeXml(e.image)}" type="image/png" />`
              : ""
      }
    </item>`
        )
        .join("\n")

    const rss = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Typhoon Roasters Blog</title>
    <link>https://typhoon.coffee/blog/</link>
    <atom:link href="https://typhoon.coffee/feed.xml" rel="self" type="application/rss+xml" />
    <description>Roasting equipment insights, coffee science, and industry guides from Typhoon Roasters.</description>
    <language>en-us</language>
    <lastBuildDate>${toRfc822(new Date())}</lastBuildDate>
${itemsXml}
  </channel>
</rss>
`

    writeFileSync(new URL("./feed.xml", import.meta.url), rss)
    console.log(`Wrote ${entries.length} items to feed.xml`)
}

main().catch((err) => {
    console.error(err)
    process.exit(1)
})
