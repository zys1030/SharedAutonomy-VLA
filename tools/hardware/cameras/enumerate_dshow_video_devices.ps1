$clsid = [Guid]'62BE5D10-60EB-11d0-BD3B-00A0C911CE86'
$type = [Type]::GetTypeFromCLSID($clsid)
$devEnum = [Activator]::CreateInstance($type)
$videoInputCategory = [Guid]'860BB310-5D01-11d0-BD3B-00A0C911CE86'
$enumMoniker = $devEnum.CreateClassEnumerator([ref]$videoInputCategory, 0)
$index = 0
while ($true) {
    $moniker = $null
    $fetched = 0
    $hr = $enumMoniker.Next(1, [ref]$moniker, [ref]$fetched)
    if ($hr -ne 0 -or $fetched -eq 0) {
        break
    }
    $bag = $null
    $propertyBagGuid = [Guid]'55272A00-42CB-11CE-8135-00AA004BB851'
    $null = $moniker.BindToStorage($null, $null, [ref]$propertyBagGuid, [ref]$bag)
    $name = $null
    $null = $bag.Read('FriendlyName', [ref]$name, $null)
    $display = $null
    $null = $moniker.GetDisplayName($null, $null, [ref]$display)
    [PSCustomObject]@{
        index         = $index
        friendly_name = [string]$name
        device_path   = [string]$display
    } | ConvertTo-Json -Compress
    $index++
}
