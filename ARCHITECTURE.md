## 🗺️ System Architecture

```mermaid
graph TD
    %% Define Styles
    classDef init fill:#f9f,stroke:#333,stroke-width:2px;
    classDef process fill:#bbf,stroke:#333,stroke-width:1px;
    classDef storage fill:#ffb,stroke:#333,stroke-width:1px;
    classDef network fill:#bfb,stroke:#333,stroke-width:1px;

    %% Concurrency Lock Stage
    Start([Execute Script]) --> LockCheck{Is .lock file present?}
    LockCheck -->|Yes| PromptUser[Interactive User Prompt / Abort]
    LockCheck -->|No| CreateLock[Create pci_concursos.lock]:::init
    
    %% Authentication & Pre-Load Phase
    CreateLock --> Auth[Authenticate Service Account via credentials.json]
    Auth --> LoadCache[Load data structures into memory]
    LoadCache --> ReadGeoCache[(Read geocodes_cache sheet)]:::storage
    LoadCache --> ReadMunRef[(Read static municipios_ref sheet)]:::storage

    %% Scraper Phase (Extract)
    ReadGeoCache --> FetchPCI[HTTP Request to PCI Concursos Sul]:::network
    FetchPCI --> BS4Parse[Parse HTML using BeautifulSoup4]:::process
    BS4Parse --> DateFilter{Is Registration Deadline Valid & >= Today?}
    DateFilter -->|No/Expired| SkipRow[Discard Opportunity]
    DateFilter -->|Yes| GenHash[Generate Unique MD5 Hash ID]:::process

    %% Geocoding Engine (Transform)
    GenHash --> GeoEngine{Resolve Coordinates}
    GeoEngine -->|Tier 1| CacheHit[Check Live In-Memory Cache]
    GeoEngine -->|Tier 2| RefHit[Check Offline municipios_ref Table]
    RefHit --> FuzzyCheck{Fuzzy string match / split string check if name complex}
    GeoEngine -->|Tier 3 Fallback| Nominatim[Query Nominatim OpenStreetMap API]:::network
    Nominatim -->|Rate Limit 429| Backoff[Exponential Backoff Retry Loop]
    Nominatim -->|Success 200| SaveNewGeo[(Append New Coordinate to geocodes_cache)]:::storage

    %% Distance Calculation
    CacheHit --> CalcDist[Apply Haversine Geodesic Distance Formula]
    RefHit --> CalcDist
    FuzzyCheck --> CalcDist
    Nominatim --> CalcDist

    %% Write Phase (Load)
    CalcDist --> PreWriteCheck{Does Hash ID exist in sheet or active batch memory?}
    PreWriteCheck -->|Yes| SkipDup[Skip Duplicate Insert]
    PreWriteCheck -->|No| WriteRow[Safe Write Append Row to live 'concursos' sheet]:::process

    %% Post-Processing Pipeline (Maintenance)
    WriteRow --> PostPipeline[Execute Automation Operations Pipeline]
    PostPipeline --> UpdateFilters[Batch Update Target Spreadsheet Filter Views Range]
    UpdateFilters --> MoveExpired[In-place Sweep: Transfer Expired Items to 'concursos_expirados' Tab]:::storage
    MoveExpired --> DedupeSweep[In-place Sweep: Deduplicate Entire Sheet by Hash ID]
    DedupeSweep --> BackfillDist[Lazy Backfill: Resolve & Update missing legacy distance matrix coordinates]
    
    %% Exit Phase
    BackfillDist --> ReleaseLock[Delete pci_concursos.lock & Release Execution Instance]:::init
    ReleaseLock --> End([Exit Execution])