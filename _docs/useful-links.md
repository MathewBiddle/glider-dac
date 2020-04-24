---
title: Links for Data Providers
wikiPageName: Links-for-Data-Providers
keywords: IOOS, documentation
tags: [getting_started, about, overview]
toc: false
#search: exclude
#permalink: index.html
summary: A list of links for Data Providers
---

<!--
> [Wiki](https://github.com/kerfoot/ioosngdac/wiki) ▸ **Data Provider Links**
-->

The following is a list of links for data providers and web applications developers that provide the means to create new deployments, upload conforming [DAC NetCDF files](ngdac-netcdf-file-format-version-2.html), view data set status and programmatically interface with the DAC via a RESTful API.

<!--
# Contents

- [Utilities and Resources for Data Providers](#utilities-and-resources-for-data-providers)
- [Links](#links)
- [Interacting with the DAC](#link-use-and-descriptions)
-->

## Data Provider Utilities

 - [**IOOS Compliance Checker**](https://compliance.ioos.us/index.html)

  The IOOS Compliance Checker is a python based tool for data providers to check for completeness and community standard compliance of local or remote netCDF files against metadata standards. This web based tool can be used to check your GliderDAC netCDF files for compliance. Be sure to select **GliderDAC** in the list of available tests to run.

 - [**FTP script**](https://github.com/ioos/ioosngdac/blob/master/util/ncFtp2ngdac.pl)

 For automating uploads of [conforming](ngdac-netcdf-file-format-version-2.html) NetCDF files to the DAC ftp server.  The script is written in Perl and requires the following non-core modules:
    + [Readonly](http://search.cpan.org/~roode/Readonly-1.03/Readonly.pm)
    + [Net::FTP](http://search.cpan.org/~shay/libnet-1.25/Net/FTP.pm)


## Submitting Data
 - [**Deployment Registration**](https://gliders.ioos.us/providers)

This link will redirect you to the GliderDAC providers page. From there you can login and create deployments. See the wiki section on [New Deplyment Registration](/ioosngdac/ngdac-netcdf-file-submission-process.html#new-deployment-registration) for more details.
 - [**FTP server**](ftp://gliders.ioos.us/)

Use this FTP server to push new data to the DAC.

## Data Access Links
Access to the datasets submitted to the DAC is provided via [ERDDAP](http://coastwatch.pfeg.noaa.gov/erddap/information.html) and [THREDDS](http://www.unidata.ucar.edu/software/thredds/current/tds/).  The access points are:

 - [**ERDDAP Catalog**](https://gliders.ioos.us/erddap/tabledap/index.html)
 - [**THREDDS Catalog**](https://gliders.ioos.us/thredds/catalog.html)

## Dataset Status
Status page for the GliderDAC
 - [**Dataset Status**](https://gliders.ioos.us/status)

## API
 - [**DAC RESTful API**](https://gliders.ioos.us/providers/api/deployment)

This endpoint provides a list of each of the glider deployments in the DAC.

## IOOS Resources

The following is a list of links to [IOOS](https://ioos.us) projects related to the DAC:

 - [**IOOS Gliders and the DAC**](https://gliders.ioos.us/data)
 - [**IOOS GliderDAC Map**](https://gliders.ioos.us/map)
 - [**IOOS Catalog**](https://data.ioos.us/)

