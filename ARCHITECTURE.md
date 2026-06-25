# System Architecture Flowchart

This document details the multi-stage ETL (Extract, Transform, Load) design of the tracking pipeline, visualizing how concurrency locks, geocoding fallbacks, and spreadsheet synchronization interact.

```mermaid
graph TD
    %% Define Styles with Explicit High-Contrast Dark Text
    classDef init fill:#f9f,stroke:#333,stroke-width:2px,color:#111;
    classDef process fill:#bbf,stroke:#333,stroke-width:1px,color:#111;
    classDef storage fill:#ffb,stroke:#333,stroke-width:1px,color:#111;
    classDef network fill:#bfb,stroke:#333,stroke-width:1px,color:#111;
    classDef conditional fill:#fff,stroke:#333,stroke-width:1px,color:#111;

    %% Concurrency Lock Stage
    Start([Execute Script]) --> LockCheck{Is .lock file present?}:::conditional
    LockCheck -->|Yes| PromptUser[Interactive User Prompt / Abort]:::process
    LockCheck -->|No| CreateLock[Create pci_concursos.lock]:::init
    
    %% Authentication & Pre-Load Phase
    CreateLock --> Auth[Authenticate Service Account via credentials.json]:::process
    Auth --> LoadCache[Load data structures into memory]:::process
    LoadCache --> ReadGeoCache[(Read geocodes_cache sheet)]:::storage
    LoadCache --> ReadMunRef[(Read static municipios_ref sheet)]:::storage

    %% Scraper Phase (Extract)
    ReadGeoCache --> FetchPCI[HTTP Request to PCI Concursos Sul]:::network
    FetchPCI --> BS4Parse[Parse HTML using BeautifulSoup4]:::process
    BS4Parse --> DateFilter{Is Registration Deadline Valid & >= Today?}:::conditional
    DateFilter -->|No/Expired| SkipRow[Discard Opportunity]:::process
    DateFilter -->|Yes| GenHash[Generate Unique MD5 Hash ID]:::process

    %% Geocoding Engine (Transform)
    GenHash --> GeoEngine{Resolve Coordinates}:::conditional
    GeoEngine -->|Tier 1| CacheHit[Check Live In-Memory Cache]:::process
    GeoEngine -->|Tier 2| RefHit[Check Offline municipios_ref Table]:::process
    RefHit --> FuzzyCheck{Fuzzy string match check}:::conditional
    GeoEngine -->|Tier 3 Fallback| Nominatim[Query Nominatim OpenStreetMap API]:::network
    Nominatim -->|Rate Limit 429| Backoff[Exponential Backoff Retry Loop]:::process
    Nominatim -->|Success 200| SaveNewGeo[(Append New Coordinate to geocodes_cache)]:::storage

    %% Distance Calculation
    CacheHit --> CalcDist[Apply Haversine Geodesic Distance Formula]:::process
    RefHit --> CalcDist
    FuzzyCheck --> CalcDist
    Nominatim --> CalcDist

    %% Write Phase (Load)
    CalcDist --> PreWriteCheck{Does Hash ID exist in sheet or active batch memory?}:::conditional
    PreWriteCheck -->|Yes| SkipDup[Skip Duplicate Insert]:::process
    PreWriteCheck -->|No| WriteRow[Safe Write Append Row to live 'concursos' sheet]:::process

    %% Post-Processing Pipeline (Maintenance)
    WriteRow --> PostPipeline[Execute Automation Operations Pipeline]:::process
    PostPipeline --> UpdateFilters[Batch Update Target Spreadsheet Filter Views Range]:::process
    UpdateFilters --> MoveExpired[In-place Sweep: Transfer Expired Items to 'concursos_expirados' Tab]:::storage
    MoveExpired --> DedupeSweep[In-place Sweep: Deduplicate Entire Sheet by Hash ID]:::process
    DedupeSweep --> BackfillDist[Lazy Backfill: Resolve & Update missing legacy distance matrix coordinates]:::process
    
    %% Exit Phase
    BackfillDist --> ReleaseLock[Delete pci_concursos.lock & Release Execution Instance]:::init
    ReleaseLock --> End([Exit Execution])