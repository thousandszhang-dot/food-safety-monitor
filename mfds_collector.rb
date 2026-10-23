#!/usr/bin/env ruby
# Read-only collector for the public MFDS Imported Food Non-compliance list.
# Run: ruby collector.rb [--limit 50] [--max-pages N]

require 'cgi'
require 'json'
require 'net/http'
require 'nokogiri'
require 'time'
require 'uri'
require 'fileutils'

BASE_URL = 'https://impfood.mfds.go.kr/CFCEE01F01/getList'
OUTPUT = File.expand_path('data/mfds_import_noncompliant.json', __dir__)
SOURCE = 'https://impfood.mfds.go.kr/CFCEE01F01'

def fetch_page(page, limit)
  uri = URI(BASE_URL)
  uri.query = URI.encode_www_form(
    'limit' => limit,
    'page' => page,
    'searchCondition' => 'pdNm',
    'srchClassCd' => ''
  )
  attempts = 0
  begin
    attempts += 1
    response = Net::HTTP.start(uri.host, uri.port, use_ssl: true,
                               open_timeout: 20, read_timeout: 40) do |http|
      request = Net::HTTP::Get.new(uri)
      request['User-Agent'] = 'FoodWatch-MFDS-Collector/1.0 (public notice archive)'
      request['Accept'] = 'text/html,application/xhtml+xml'
      http.request(request)
    end
    raise "MFDS returned HTTP #{response.code}" unless response.is_a?(Net::HTTPSuccess)
    response.body
  rescue StandardError
    raise if attempts >= 3
    sleep(attempts * 2)
    retry
  end
end

def field_map(node)
  node.css('li').each_with_object({}) do |li, out|
    label = li.at_css('strong')&.text&.strip
    value = li.at_css('span')&.text&.strip
    out[label] = value if label && value
  end
end

def parse_record(node)
  values = field_map(node)
  product_name = node.at_css('h4 strong')&.text&.strip
  english_name = node.at_css('h4 span')&.text&.strip
  violation_link = node.css('dd a[id]').find { |a| a['id'].to_s.match?(/\A\d+\z/) }
  violation = values['위반내역(시험항목/기준/결과)'] || violation_link&.text.to_s.gsub(/위반내역\(시험항목\/기준\/결과\)/, '').strip
  notice_id = violation_link&.[]('id')

  detected, basis = parse_measurement(violation)

  {
    'id' => notice_id,
    'sourceCountry' => 'KR',
    'sourceCountryName' => '韩国',
    'noticeDate' => values['부적합일자'],
    'importingCountry' => '韩国',
    'originCountry' => values['제조국가'],
    'originCountrySourceLabel' => '制造国家（MFDS 原字段）',
    'productName' => product_name,
    'productNameEnglish' => english_name,
    'productCategory' => values['제품구분'],
    'productType' => values['품목명'],
    'nonComplianceText' => violation,
    'detectedValueText' => detected&.strip,
    'complianceStandardText' => basis&.strip,
    'handlingAgency' => values['처리기관'],
    'manufactureExpiryText' => values['제조일자/소비기한'],
    'sourceUrl' => SOURCE,
    'recordUrl' => notice_id ? "#{SOURCE}#notice-#{notice_id}" : SOURCE,
    'fieldExtraction' => 'detectedValueText/complianceStandardText are best-effort parses; nonComplianceText is authoritative source text'
  }
end

def embed_snapshot(data)
  page = File.expand_path('index.html', __dir__)
  html = File.read(page)
  pattern = /(<script id="mfds-snapshot" type="application\/json">)(.*?)(<\/script>)/m
  raise 'Snapshot placeholder not found in index.html' unless html.match?(pattern)
  previous = html.match(pattern)&.[](2)
  if previous && !previous.empty?
    translations = JSON.parse(previous).fetch('records', []).each_with_object({}) do |record, out|
      out[record['id']] = record['productNameZh'] if record['id'] && record['productNameZh']
    end
    data.fetch('records', []).each do |record|
      record['productNameZh'] ||= translations[record['id']]
    end
  end
  json = JSON.generate(data).gsub('<', '\\u003c')
  updated = html.sub(pattern) { "#{$1}#{json}#{$3}" }
  temporary = "#{page}.tmp"
  File.write(temporary, updated)
  File.rename(temporary, page)
end

def parse_measurement(text)
  ordered = text.to_s.match(/(?:기준규격|규격|기준)\s*[:：]\s*(.+?)\s*[,，]\s*결과\s*[:：]\s*(.+?)(?:\)|$)/)
  return [ordered[2].strip, ordered[1].strip] if ordered
  reverse = text.to_s.match(/([-+]?\d+(?:\.\d+)?\s*(?:mg\/kg|μg\/g|mcg\/g|g\/kg|mg\/L|g\/L|CFU\/g)(?:\s*검출)?)[^\[]*\[(?:기준규격|규격|기준)\s*[:：]\s*([^\]]+)\]/i)
  reverse ? [reverse[1].strip, reverse[2].strip] : [nil, nil]
end

def main
  if ARGV.delete('--embed-existing')
    raise "No existing snapshot at #{OUTPUT}" unless File.file?(OUTPUT)
    embed_snapshot(JSON.parse(File.read(OUTPUT)))
    puts "Embedded existing MFDS snapshot in index.html."
    return
  end
  if ARGV.delete('--reparse-existing')
    raise "No existing snapshot at #{OUTPUT}" unless File.file?(OUTPUT)
    data = JSON.parse(File.read(OUTPUT))
    data.fetch('records').each do |record|
      record['detectedValueText'], record['complianceStandardText'] = parse_measurement(record['nonComplianceText'])
    end
    data['collectedAt'] = Time.now.utc.iso8601
    File.write(OUTPUT, JSON.pretty_generate(data) + "\n")
    embed_snapshot(data)
    puts "Re-parsed and embedded #{data['records'].length} existing MFDS notices."
    return
  end
  limit = 50
  max_pages = nil
  args = ARGV.dup
  until args.empty?
    key = args.shift
    case key
    when '--limit' then limit = Integer(args.shift || 50)
    when '--max-pages' then max_pages = Integer(args.shift)
    else raise "Unknown option: #{key}"
    end
  end
  raise 'limit must be 1..50' unless (1..50).cover?(limit)

  first = Nokogiri::HTML(fetch_page(1, limit))
  content = first.at_css('#content') || first
  total_match = content.text.match(/총\s*([\d,]+)\s*건/)
  total = total_match ? total_match[1].delete(',').to_i : nil
  pages = total ? (total.to_f / limit).ceil : 1
  pages = [pages, max_pages].min if max_pages
  collected = []

  pages.times do |index|
    page = index + 1
    html = page == 1 ? first : Nokogiri::HTML(fetch_page(page, limit))
    records = html.css('#content .gallery > ul > li').map { |node| parse_record(node) }
    collected.concat(records)
    warn "MFDS page #{page}/#{pages}: #{records.length} records"
    sleep 0.7 if page < pages
  end

  collected.reject! { |record| record['productName'].to_s.empty? || record['noticeDate'].to_s.empty? }
  deduped = collected.uniq { |record| record['id'] || [record['noticeDate'], record['productName'], record['nonComplianceText']].join('|') }
  data = {
    'source' => 'MFDS Imported Food Non-compliance',
    'sourceUrl' => SOURCE,
    'collectedAt' => Time.now.utc.iso8601,
    'publishedTotalAtCollection' => total,
    'recordsCollected' => deduped.length,
    'records' => deduped
  }
  FileUtils.mkdir_p(File.dirname(OUTPUT))
  temporary = "#{OUTPUT}.tmp"
  File.write(temporary, JSON.pretty_generate(data) + "\n")
  File.rename(temporary, OUTPUT)
  embed_snapshot(data)
  puts "Saved #{deduped.length} MFDS notices to #{OUTPUT} (official list total: #{total || 'unknown'})."
end

main
