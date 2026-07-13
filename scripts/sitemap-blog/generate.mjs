import { connect } from "framer-api"
import { writeFileSync } from "fs"

const PROJECT_URL = process.env.FRAMER_PROJECT_URL
const API_KEY = process.env.FRAMER_API_KEY
const ARTICLES_COLLECTION_ID = "yY_gb2Idp"
const FIELD_DATE = "PP1bxVtET"

function escapeXml(str) {
    return String(str ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
}

function fieldValue(item, fieldId) {
    return item.fieldData?.[fieldId]?.value
}

function toDateOnly(date) {
    return date.toISOString().slice(0, 10)
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
        .map((it) => ({
            loc: `https://typhoon.coffee/blog/${it.slug}`,
            date: fieldValue(it, FIELD_DATE) ? new Date(fieldValue(it, FIELD_DATE)) : null,
        }))
        .filter((e) => e.date)
        .sort((a, b) => b.date - a.date)

    const latest = entries[0]?.date ?? new Date()

    const urlsXml = [
        `  <url><loc>https://typhoon.coffee/blog/</loc><lastmod>${toDateOnly(latest)}</lastmod></url>`,
        ...entries.map(
            (e) => `  <url><loc>${escapeXml(e.loc)}</loc><lastmod>${toDateOnly(e.date)}</lastmod></url>`
        ),
    ].join("\n")

    const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urlsXml}
</urlset>
`

    writeFileSync(new URL("./sitemap-blog.xml", import.meta.url), xml)
    console.log(`Wrote ${entries.length} blog URLs to sitemap-blog.xml`)
}

main().catch((err) => {
    console.error(err)
    process.exit(1)
})
